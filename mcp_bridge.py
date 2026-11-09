import json
import re
import shlex
import shutil
import sys
from contextlib import AsyncExitStack

from langchain_core.tools import StructuredTool
from mcp import Client, StdioServerParameters

import config

LOCAL = StdioServerParameters(
    command=sys.executable,
    args=[str(config.ROOT / "releasecheck_server.py")],
    cwd=str(config.ROOT),
)

EXPLORE_LOCAL_TOOLS = {"list_files", "read_file", "search_text"}


def rea_params() -> StdioServerParameters:
    parts = shlex.split(config.REA_COMMAND, posix=True)
    exe = shutil.which(parts[0]) or shutil.which(parts[0] + ".cmd") or parts[0]
    return StdioServerParameters(command=exe, args=parts[1:])


def unpack(result):
    if getattr(result, "is_error", False):
        texts = [c.text for c in (result.content or []) if hasattr(c, "text")]
        raise RuntimeError(" ".join(texts) or "MCP tool failed")
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        if isinstance(sc, dict) and set(sc) == {"result"}:
            return sc["result"]
        return sc
    texts = [c.text for c in (result.content or []) if hasattr(c, "text")]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return joined


def _schema(tool) -> dict:
    s = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    if hasattr(s, "model_dump"):
        s = s.model_dump()
    s = dict(s)
    s.setdefault("type", "object")
    s.setdefault("properties", {})
    return s


def _safe_name(prefix: str, name: str) -> str:
    return (prefix + re.sub(r"[^A-Za-z0-9_-]", "_", name))[:64]


class Toolbox:
    def __init__(self, use_rea: bool = False):
        self.use_rea = use_rea
        self.stack = AsyncExitStack()
        self.local = None
        self.rea = None
        self.rea_error = ""

    async def __aenter__(self):
        self.local = await self.stack.enter_async_context(Client(LOCAL))
        if self.use_rea:
            try:
                self.rea = await self.stack.enter_async_context(Client(rea_params()))
            except Exception as e:
                self.rea = None
                self.rea_error = str(e)[:300]
        return self

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def call(self, name: str, args: dict | None = None):
        return unpack(await self.local.call_tool(name, args or {}))

    async def _wrap(self, client, prefix: str, allow: set | None):
        listed = await client.list_tools()
        out = []
        for t in listed.tools:
            if allow and t.name not in allow:
                continue

            async def run(_client=client, _name=t.name, **kwargs):
                try:
                    data = unpack(await _client.call_tool(_name, kwargs))
                except Exception as e:
                    return f"ERROR: {e}"
                text = data if isinstance(data, str) else json.dumps(data, default=str)
                return text[:3000]

            out.append(StructuredTool.from_function(
                coroutine=run,
                name=_safe_name(prefix, t.name),
                description=(t.description or t.name)[:1000],
                args_schema=_schema(t),
            ))
        return out

    async def explore_tools(self) -> list[StructuredTool]:
        tools = await self._wrap(self.local, "", EXPLORE_LOCAL_TOOLS)
        if self.rea:
            try:
                tools += await self._wrap(self.rea, "rea_", None)
            except Exception as e:
                self.rea_error = str(e)[:300]
        return tools
