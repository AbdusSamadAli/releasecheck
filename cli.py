import argparse
import asyncio
import sys
import uuid

from langgraph.types import Command

from graph import graph


async def run(path: str, deep: bool, keep_all: bool):
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    async for update in graph.astream({"build_path": path, "deep": deep}, cfg, stream_mode="updates"):
        for node, data in update.items():
            if node == "__interrupt__":
                continue
            for s in (data or {}).get("steps", []):
                print(f"[{node}] {s}")
    state = (await graph.aget_state(cfg)).values
    issues = state.get("issues", [])
    print("\n" + state.get("summary", ""))
    for i in issues:
        print(f"  #{i['id']} [{i['priority']}] {i['title']} -> {i['file']}" + (f":{i['line']}" if i["line"] else ""))
    keep = [i["id"] for i in issues]
    if not keep_all and issues:
        raw = input("\nIds to drop as 'not a problem' (comma separated, blank = keep all): ").strip()
        drop = {int(x) for x in raw.split(",") if x.strip().isdigit()}
        keep = [k for k in keep if k not in drop]
    async for update in graph.astream(Command(resume=keep), cfg, stream_mode="updates"):
        for node, data in update.items():
            for s in (data or {}).get("steps", []):
                print(f"[{node}] {s}")
    final = (await graph.aget_state(cfg)).values
    print("\n" + final["report_md"])


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="ReleaseCheck from the terminal")
    p.add_argument("path")
    p.add_argument("--deep", action="store_true")
    p.add_argument("--yes", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.path, a.deep, a.yes))


if __name__ == "__main__":
    main()
