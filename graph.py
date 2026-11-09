import json
import operator
import re
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

import build_tools as bt
import config
from mcp_bridge import Toolbox

PRIORITY_ORDER = {"Must fix": 0, "Should fix": 1, "Nice to fix": 2}


class Extra(BaseModel):
    title: str = Field(description="Short plain-English title")
    priority: Literal["Must fix", "Should fix", "Nice to fix"]
    file: str = Field(description="Path of the file inside the build, exactly as shown by the tools")
    evidence: str = Field(description="Exact text copied from that file, one line")
    why: str = Field(description="Why it matters, one sentence, no jargon")
    fix: str = Field(description="How to fix it, one sentence")


class ExtraList(BaseModel):
    items: list[Extra] = Field(default_factory=list)


class Note(BaseModel):
    id: int
    explanation: str = Field(description="One or two plain sentences about this specific item")


class Summary(BaseModel):
    summary: str = Field(description="Two to four sentence overview for the developer")
    notes: list[Note] = Field(default_factory=list)


class State(TypedDict, total=False):
    build_path: str
    deep: bool
    build_id: str
    name: str
    files: int
    size_mb: float
    issues: list[dict]
    dropped: int
    summary: str
    accepted: list[int]
    verdict: str
    report_md: str
    report_id: int
    steps: Annotated[list[str], operator.add]


def mask(text: str) -> str:
    return re.sub(r"[A-Za-z0-9_\-+/=]{8,}", lambda m: m.group(0)[:4] + "****", text or "")


def shown_evidence(issue: dict) -> str:
    secretish = issue["check_id"] == "secret_key" or re.search(r"(?i)key|secret|passw|token|credential", issue["title"])
    return mask(issue["evidence"]) if secretish else issue["evidence"]


def _llm(effort: str | None = None):
    from langchain_groq import ChatGroq
    extra = {"reasoning_effort": effort} if effort else {}
    return ChatGroq(model=config.GROQ_MODEL, temperature=0.1, api_key=config.GROQ_API_KEY, max_retries=3, **extra)


def _example(model_cls) -> str:
    def shape(m):
        out = {}
        for name, f in m.model_fields.items():
            ann = f.annotation
            args = getattr(ann, "__args__", ())
            inner = args[0] if args else None
            if isinstance(inner, type) and issubclass(inner, BaseModel):
                out[name] = [shape(inner)]
            elif getattr(ann, "__origin__", None) is Literal:
                out[name] = " | ".join(args)
            else:
                out[name] = f.description or ann.__name__ if hasattr(ann, "__name__") else str(ann)
        return out
    return json.dumps(shape(model_cls), indent=1)


def _parse_json(text: str):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    end = text.rfind("}")
    return json.loads(text[start:end + 1])


async def structured(model_cls, messages, tries: int = 2, effort: str | None = None):
    llm = _llm(effort)
    ask = messages + [HumanMessage("Reply with ONLY a JSON object of this shape, no other text:\n" + _example(model_cls))]
    last = None
    for _ in range(tries):
        try:
            res = await llm.ainvoke(ask)
            return model_cls.model_validate(_parse_json(res.content))
        except Exception as e:
            last = e
    raise last


async def open_build(state: State) -> State:
    async with Toolbox() as tb:
        info = await tb.call("open_build", {"path": state["build_path"]})
    return {**info, "steps": [f"Opened {info['name']}: {info['files']} files, {info['size_mb']} MB"]}


async def run_checklist(state: State) -> State:
    async with Toolbox() as tb:
        issues = await tb.call("run_checklist", {"build_id": state["build_id"]})
    for i, it in enumerate(issues):
        it["id"] = i
    return {"issues": issues, "steps": [f"Checklist found {len(issues)} item(s)"]}


def route_after_checklist(state: State) -> str:
    return "explore" if state.get("deep") and config.llm_available() else "verify"


EXPLORE_PROMPT = """You help a developer get their own app build ready for release.
The build is unpacked at: {build_dir}
build_id: {build_id}

The automatic checklist already found:
{found}

Look for anything important the checklist may have missed, in these plain categories only:
secret keys or passwords, debug/developer settings left on, test or local addresses,
test/mock/fake/sandbox modes left on (for example fake payments or mock data),
files that should not ship, unfinished features. Start with list_files, then read the
most relevant config or main files. Tools starting with rea_ come from the REA analysis
server; use them if they help, passing the build folder path above.
Only report things you actually saw in tool output. Keep it short."""


async def explore(state: State) -> State:
    try:
        return await _explore(state)
    except Exception as e:
        msg = str(e).splitlines()[0][:120]
        return {"issues": state["issues"], "steps": [f"Deep look skipped ({type(e).__name__}: {msg})"]}


async def _explore(state: State) -> State:
    found = "\n".join(f"- {i['file']}" + (f" line {i['line']}" if i["line"] else "") + f": {shown_evidence(i)[:80]} ({i['title']})"
                      for i in state["issues"][:30]) or "- nothing"
    build_dir = str((config.BUILDS_DIR / state["build_id"]).resolve())
    llm = _llm()
    async with Toolbox(use_rea=config.REA_ENABLED) as tb:
        tools = await tb.explore_tools()
        by_name = {t.name: t for t in tools}
        msgs = [SystemMessage(EXPLORE_PROMPT.format(build_dir=build_dir, build_id=state["build_id"], found=found)),
                HumanMessage("Start reviewing the build.")]
        bound = llm.bind_tools(tools)
        calls = 0
        seen = []
        loop_note = ""
        for _ in range(config.EXPLORE_STEPS):
            try:
                ai = await bound.ainvoke(msgs)
            except Exception as e:
                loop_note = f", agent stopped early ({type(e).__name__})"
                break
            msgs.append(ai)
            if not ai.tool_calls:
                break
            for tc in ai.tool_calls:
                calls += 1
                seen.append(tc["name"] + (f"({tc['args'].get('path') or tc['args'].get('text')})" if tc["args"].get("path") or tc["args"].get("text") else ""))
                t = by_name.get(tc["name"])
                out = await t.ainvoke(tc["args"]) if t else f"ERROR: unknown tool {tc['name']}"
                msgs.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))
        rea_note = " (REA connected)" if tb.rea else (f" (REA unavailable: {tb.rea_error[:80]})" if config.REA_ENABLED else "")
    transcript = []
    for m in msgs[2:]:
        if isinstance(m, ToolMessage):
            transcript.append(f"TOOL OUTPUT:\n{str(m.content)[:3000]}")
        elif getattr(m, "content", None):
            transcript.append(f"AGENT NOTE: {str(m.content)[:1500]}")
    extract_msgs = [
        SystemMessage("You are reviewing an app build that is about to be released to real customers. "
                      "Go through EVERY line of every file in the TOOL OUTPUT and ask: would this line be wrong or risky "
                      "in a real customer release? Examples: secret values, debug switches, local or test addresses, "
                      "settings that turn on test, mock, fake, demo or sandbox behaviour, disabled real features, unfinished work.\n"
                      "These lines are ALREADY reported, do not repeat them:\n" + found +
                      "\nReport every OTHER line that is a problem. Copy the evidence exactly from one line of tool output, "
                      "without the line number prefix. Use an empty list only if truly nothing else is wrong."),
        HumanMessage("\n\n".join(transcript)[-12000:] or "No tool output."),
    ]
    parse_note = ""
    try:
        extra = await structured(ExtraList, extract_msgs, effort="medium")
        items = extra.items
    except Exception as e:
        items = []
        parse_note = f", could not read agent answer ({type(e).__name__}: {str(e)[:150]})"
    start = len(state["issues"])
    new = [{"id": start + k, "check_id": "agent", "title": e.title, "priority": e.priority, "why": e.why,
            "fix": e.fix, "file": e.file.lstrip("/"), "line": None, "evidence": re.sub(r"^\d+:\s?", "", e.evidence).strip(),
            "source": "agent"} for k, e in enumerate(items)]
    return {"issues": state["issues"] + new,
            "steps": [f"Deep look used {calls} tool call(s) [{', '.join(seen[:10])}]{rea_note}{loop_note}, suggested {len(new)} extra item(s){parse_note}"]}


async def verify(state: State) -> State:
    kept, dropped = [], 0
    for it in state["issues"]:
        if it["source"] == "checklist" or bt.evidence_exists(state["build_id"], it["file"], it["evidence"]):
            kept.append(it)
        else:
            dropped += 1
    kept.sort(key=lambda x: (PRIORITY_ORDER.get(x["priority"], 3), x["file"], x["line"] or 0))
    for i, it in enumerate(kept):
        it["id"] = i
    return {"issues": kept, "dropped": dropped,
            "steps": [f"Verified evidence: kept {len(kept)}, removed {dropped} unproven"]}


def _fallback_summary(state: State) -> str:
    n = {p: sum(1 for i in state["issues"] if i["priority"] == p) for p in PRIORITY_ORDER}
    if not state["issues"]:
        return f"{state['name']} looks ready: the checklist found nothing to fix."
    return (f"{state['name']} has {n['Must fix']} must-fix, {n['Should fix']} should-fix and "
            f"{n['Nice to fix']} nice-to-fix item(s). Start with the must-fix items.")


async def explain(state: State) -> State:
    summary = _fallback_summary(state)
    how = "built-in"
    if config.llm_available() and state["issues"]:
        rows = [{"id": i["id"], "title": i["title"], "priority": i["priority"], "file": i["file"],
                 "line": i["line"], "evidence": shown_evidence(i)[:160]} for i in state["issues"][:40]]
        try:
            res = await structured(Summary, [
                SystemMessage("You explain release-check results to a developer in plain, friendly English. "
                              "Use only the items given. Never add new items or numbers. No security jargon."),
                HumanMessage(f"App: {state['name']} ({state['files']} files)\nItems:\n{json.dumps(rows)}"),
            ])
            summary = res.summary
            notes = {n.id: n.explanation for n in res.notes}
            for it in state["issues"]:
                if it["id"] in notes:
                    it["explanation"] = notes[it["id"]]
            how = "AI"
        except Exception as e:
            how = f"built-in, AI unavailable: {type(e).__name__}"
    return {"summary": summary, "issues": state["issues"], "steps": [f"Wrote plain-English summary ({how})"]}


async def review(state: State) -> State:
    accepted = interrupt({"issues": state["issues"], "summary": state["summary"]})
    if accepted is None:
        accepted = [i["id"] for i in state["issues"]]
    return {"accepted": list(accepted), "steps": [f"Developer kept {len(accepted)} of {len(state['issues'])} item(s)"]}


def verdict_for(kept: list[dict]) -> str:
    if any(i["priority"] == "Must fix" for i in kept):
        return "FAIL"
    if kept:
        return "PASS WITH NOTES"
    return "PASS"


def render_report(state: State, kept: list[dict], verdict: str) -> str:
    lines = [f"# Release check: {state['name']}", "",
             f"**Result: {verdict}**  ", f"Files: {state['files']} | Size: {state['size_mb']} MB | Build id: `{state['build_id']}`", "",
             "## Summary", "", state["summary"], ""]
    for p in PRIORITY_ORDER:
        group = [i for i in kept if i["priority"] == p]
        if not group:
            continue
        lines += [f"## {p} ({len(group)})", ""]
        for i in group:
            where = f"{i['file']}" + (f", line {i['line']}" if i["line"] else "")
            lines += [f"### {i['title']}", f"- **Where:** `{where}`",
                      f"- **Found:** `{shown_evidence(i).replace('`', '')[:160]}`",
                      f"- **Why it matters:** {i.get('explanation') or i['why']}",
                      f"- **How to fix:** {i['fix']}", ""]
    rejected = len(state["issues"]) - len(kept)
    if rejected:
        lines += [f"_{rejected} item(s) marked as not a problem by the developer._", ""]
    return "\n".join(lines)


async def report(state: State) -> State:
    keep = set(state["accepted"])
    kept = [i for i in state["issues"] if i["id"] in keep]
    verdict = verdict_for(kept)
    md = render_report(state, kept, verdict)
    async with Toolbox() as tb:
        saved = await tb.call("save_report", {"build_id": state["build_id"], "name": state["name"],
                                              "verdict": verdict, "issues": len(kept), "report_md": md})
    return {"verdict": verdict, "report_md": md, "report_id": saved["report_id"],
            "steps": [f"Result: {verdict} (report #{saved['report_id']} saved)"]}


def build_graph():
    g = StateGraph(State)
    g.add_node("open_build", open_build)
    g.add_node("run_checklist", run_checklist)
    g.add_node("explore", explore)
    g.add_node("verify", verify)
    g.add_node("explain", explain)
    g.add_node("review", review)
    g.add_node("report", report)
    g.add_edge(START, "open_build")
    g.add_edge("open_build", "run_checklist")
    g.add_conditional_edges("run_checklist", route_after_checklist, {"explore": "explore", "verify": "verify"})
    g.add_edge("explore", "verify")
    g.add_edge("verify", "explain")
    g.add_edge("explain", "review")
    g.add_edge("review", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=MemorySaver())


graph = build_graph()
