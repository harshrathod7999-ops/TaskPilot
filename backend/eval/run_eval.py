"""TaskPilot agent evaluation harness.

Runs the real LangGraph agent on each task (auto-approving any write actions,
the harness plays the human) and scores:

  * tool_selection_accuracy — of the tools a task *should* use, how many did the
    agent actually call? (deterministic, read straight from the trace)
  * task_success           — did the final answer accomplish the task? Judged by
    a free Gemini Flash LLM-as-judge.
  * avg_steps, error_rate, approval_rate — operational health.

Requires GROQ_API_KEY (agent) and GEMINI_API_KEY (judge + fallback). Web tasks
hit the live internet, so exact wording varies — that's why success is judged,
not string-matched. Results are written to eval/results.md.

    python eval/run_eval.py                  # all tasks
    python eval/run_eval.py --limit 5        # quick subset
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.types import Command  # noqa: E402

from app.agent.graph import get_graph  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm_compat import chat  # noqa: E402

EVAL_DIR = Path(__file__).parent
DATASET = EVAL_DIR / "dataset.json"
RESULTS = EVAL_DIR / "results.md"


async def run_task(task: str, max_steps: int = 8) -> dict:
    """Drive the agent to completion, auto-approving write actions."""
    graph = await get_graph()
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}, "recursion_limit": 60}
    tools_used: list[str] = []
    final: str | None = None
    approvals = 0
    errored = False

    current: object = {"task": task, "max_steps": max_steps, "max_critiques": 1}
    for _ in range(10):  # bounded resume loop
        interrupted = False
        async for mode, chunk in graph.astream(current, cfg, stream_mode=["custom", "updates"]):
            if mode == "custom":
                t = chunk.get("type")
                d = chunk.get("data", {})
                if t == "action":
                    tools_used.append(d.get("tool"))
                elif t == "final":
                    final = d.get("text")
                elif t == "error":
                    errored = True
            elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                interrupted = True
        if interrupted:
            approvals += 1
            current = Command(resume={"approved": True})  # harness approves
            continue
        break

    return {
        "tools_used": tools_used,
        "final": final or "",
        "steps": len(tools_used),
        "approvals": approvals,
        "errored": errored,
    }


def judge_success(task: str, answer: str) -> bool:
    if not answer:
        return False
    msgs = [
        {"role": "system", "content": "You judge whether an AI agent's answer "
         "accomplishes the user's task. Reply with strict JSON: {\"success\": true|false, "
         "\"reason\": \"...\"}. Be lenient about phrasing, strict about whether the task "
         "was actually done."},
        {"role": "user", "content": f"TASK: {task}\n\nAGENT ANSWER:\n{answer}\n\nDid it accomplish the task?"},
    ]
    try:
        raw = chat(msgs, model="gemini/gemini-3.5-flash", fallback_model=None,
                   temperature=0, max_tokens=150)
        start, end = raw.find("{"), raw.rfind("}")
        return bool(json.loads(raw[start:end + 1]).get("success"))
    except Exception:
        return False


def tool_score(expected: list[str], used: list[str]) -> float:
    if not expected:
        return 1.0
    used_set = set(used)
    return sum(1 for t in expected if t in used_set) / len(expected)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not os.getenv("GROQ_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        print("ERROR: set GROQ_API_KEY and GEMINI_API_KEY in your .env to run the agent eval.")
        return 1

    tasks = json.loads(DATASET.read_text(encoding="utf-8"))["tasks"]
    if args.limit:
        tasks = tasks[: args.limit]

    settings = get_settings()
    rows = []
    print(f"Running {len(tasks)} tasks…")
    for case in tasks:
        res = await run_task(case["task"], settings.agent_max_steps)
        tsel = tool_score(case["expected_tools"], res["tools_used"])
        success = judge_success(case["task"], res["final"])
        rows.append({**case, **res, "tool_selection": tsel, "success": success})
        flag = "OK " if success else "XX "
        print(f"  {flag}{case['id']} [{case['category']}] tools={res['tools_used']} "
              f"sel={tsel:.2f} success={success}")

    n = len(rows)
    succ = sum(r["success"] for r in rows) / n
    sel = sum(r["tool_selection"] for r in rows) / n
    avg_steps = sum(r["steps"] for r in rows) / n
    err = sum(r["errored"] for r in rows) / n
    appr = sum(1 for r in rows if r["approvals"]) / n

    lines = [
        "# TaskPilot — Agent Evaluation Results", "",
        f"Ran **{n}** tasks (research, deep-research, action, mixed, robustness).", "",
        "| Metric | Score |", "|--------|------:|",
        f"| Task success (LLM-judged) | {succ*100:.1f}% |",
        f"| Tool-selection accuracy | {sel*100:.1f}% |",
        f"| Avg tool calls / task | {avg_steps:.1f} |",
        f"| Tasks that hit an error event | {err*100:.1f}% |",
        f"| Tasks that triggered approval gate | {appr*100:.1f}% |",
        "",
        "## Per-task", "",
        "| ID | Category | Tools used | Tool-sel | Success |",
        "|----|----------|-----------|---------:|:-------:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['category']} | {', '.join(r['tools_used']) or '—'} "
            f"| {r['tool_selection']*100:.0f}% | {'✅' if r['success'] else '❌'} |"
        )
    RESULTS.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSuccess {succ*100:.1f}% · Tool-selection {sel*100:.1f}% · wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
