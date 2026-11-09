import asyncio
import re
import runpy
import uuid

import pandas as pd
import streamlit as st
from langgraph.types import Command

import build_tools as bt
import config
from graph import graph, shown_evidence

st.set_page_config(page_title="ReleaseCheck", page_icon="✅", layout="wide")

if not (config.SAMPLES_DIR / "demo_app.zip").exists():
    runpy.run_path(str(config.SAMPLES_DIR / "make_samples.py"), run_name="__main__")

SAMPLES = {"Demo app (has problems)": "demo_app.zip", "Clean app (should pass)": "clean_app.zip"}
BADGE = {"PASS": ("✅", "success"), "PASS WITH NOTES": ("🟡", "warning"), "FAIL": ("❌", "error")}

ss = st.session_state
ss.setdefault("stage", "start")
ss.setdefault("steps", [])


async def _stream(inp, cfg, box):
    async for update in graph.astream(inp, cfg, stream_mode="updates"):
        for node, data in update.items():
            if node == "__interrupt__" or not isinstance(data, dict):
                continue
            for s in data.get("steps", []):
                ss.steps.append(s)
                box.write(f"**{node}** - {s}")
    return (await graph.aget_state(cfg)).values


def run_graph(inp, label):
    with st.status(label, expanded=True) as box:
        for s in ss.steps:
            box.write(s)
        try:
            values = asyncio.run(_stream(inp, ss.cfg, box))
        except Exception as e:
            box.update(label="Something went wrong", state="error")
            st.error(f"{type(e).__name__}: {e}")
            st.stop()
        box.update(label="Done", state="complete", expanded=False)
    return values


def save_upload(f) -> str:
    if f.size > config.MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"File is larger than {config.MAX_UPLOAD_MB} MB")
        st.stop()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", f.name)
    path = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
    path.write_bytes(f.getbuffer())
    return str(path)


def reset():
    for k in ("stage", "steps", "cfg", "result"):
        ss.pop(k, None)


with st.sidebar:
    st.header("ReleaseCheck")
    st.caption("Check your own app build before you ship it.")
    st.write("AI explanations:", "🟢 on" if config.llm_available() else "⚪ off (add GROQ_API_KEY)")
    st.write("REA server:", "🟢 enabled" if config.REA_ENABLED else "⚪ off (set REA_ENABLED=true)")
    st.divider()
    st.subheader("Recent checks")
    hist = bt.get_history(8)
    if hist:
        for h in hist:
            st.write(f"{BADGE.get(h['verdict'], ('•',))[0]} #{h['id']} {h['name']} - {h['issues']} item(s)")
    else:
        st.caption("No checks yet.")
    if ss.stage != "start":
        st.button("New check", on_click=reset, width="stretch")

st.title("✅ ReleaseCheck")
st.write("Upload a build of **your own app** (a .zip, .apk, .jar or similar). ReleaseCheck looks inside it for leftover keys, "
         "debug settings, test addresses and files that should not ship. Then it gives you a clear PASS or FAIL report.")

if ss.stage == "start":
    c1, c2 = st.columns([2, 1])
    with c1:
        up = st.file_uploader("Your build", type=sorted(e.strip(".") for e in bt.ARCHIVE_EXT))
    with c2:
        sample = st.selectbox("...or try a sample", ["None"] + list(SAMPLES))
    deep = st.toggle("Deep look with AI agent (slower)", value=False, disabled=not config.llm_available(),
                     help="An AI agent reads the build with MCP tools (and REA if enabled) to spot things the checklist missed.")
    consent = st.checkbox("This is my own app, or I have permission to check it.")
    if st.button("Check build", type="primary", disabled=not consent or (up is None and sample == "None")):
        path = save_upload(up) if up else str(config.SAMPLES_DIR / SAMPLES[sample])
        ss.cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
        ss.steps = []
        ss.result = run_graph({"build_path": path, "deep": deep}, "Checking build...")
        ss.stage = "review"
        st.rerun()

elif ss.stage == "review":
    v = ss.result
    with st.expander("What the agent did", expanded=False):
        for s in ss.steps:
            st.write("•", s)
    st.subheader("Summary")
    st.info(v.get("summary", ""))
    issues = v.get("issues", [])
    if not issues:
        st.success("Nothing found.")
        keep_ids = []
    else:
        st.subheader("Review the findings")
        st.caption("Untick anything that is not a real problem for your app, then create the report.")
        df = pd.DataFrame([{
            "Keep": True, "id": i["id"], "Priority": i["priority"], "Problem": i["title"],
            "Where": i["file"] + (f":{i['line']}" if i["line"] else ""), "Found": shown_evidence(i)[:120],
            "Why": i.get("explanation") or i["why"], "Source": i["source"],
        } for i in issues])
        edited = st.data_editor(df, hide_index=True, width="stretch",
                                disabled=[c for c in df.columns if c != "Keep"],
                                column_config={"id": None, "Why": st.column_config.TextColumn(width="large")})
        keep_ids = [int(x) for x in edited.loc[edited["Keep"], "id"]]
    if st.button("Create report", type="primary"):
        ss.result = run_graph(Command(resume=keep_ids), "Writing report...")
        ss.stage = "done"
        st.rerun()

elif ss.stage == "done":
    v = ss.result
    icon, kind = BADGE.get(v["verdict"], ("•", "info"))
    getattr(st, kind)(f"{icon} Result: **{v['verdict']}**")
    st.download_button("Download report (.md)", v["report_md"], file_name=f"release_check_{v['build_id']}.md",
                       mime="text/markdown")
    st.markdown(v["report_md"])
