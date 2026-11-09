import argparse
import asyncio
import json
import sys
import uuid

import build_tools as bt
import config

AI_EXPECTED = {"demo_app.zip": "payments: { mode: 'fake'"}


def checklist_eval():
    expected = json.loads((config.SAMPLES_DIR / "expected.json").read_text(encoding="utf-8"))
    total_hit = total_exp = total_extra = 0
    for name, exp in expected.items():
        info = bt.open_build(str(config.SAMPLES_DIR / name))
        found = {i["check_id"] for i in bt.run_checklist(info["build_id"])}
        hit = found & set(exp)
        extra = found - set(exp)
        total_hit += len(hit)
        total_exp += len(exp)
        total_extra += len(extra)
        print(f"{name}: expected {sorted(exp)} | found {sorted(found)} | missed {sorted(set(exp) - found)} | false alarms {sorted(extra)}")
    recall = total_hit / total_exp if total_exp else 1.0
    print(f"Checklist recall: {total_hit}/{total_exp} = {recall:.0%} | False alarm checks: {total_extra}")


async def ai_eval():
    from graph import graph
    for name, needle in AI_EXPECTED.items():
        cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
        await graph.ainvoke({"build_path": str(config.SAMPLES_DIR / name), "deep": True}, cfg)
        state = (await graph.aget_state(cfg)).values
        agent = [i for i in state["issues"] if i["source"] == "agent"]
        caught = any(needle in i["evidence"] for i in agent)
        print(f"{name}: AI deep look {'caught' if caught else 'MISSED'} the hidden issue | agent items kept: {len(agent)} | removed as unproven: {state.get('dropped', 0)}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--ai", action="store_true")
    a = p.parse_args()
    checklist_eval()
    if a.ai:
        asyncio.run(ai_eval())


if __name__ == "__main__":
    main()
