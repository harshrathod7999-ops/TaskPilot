# TaskPilot — Autonomous Research & Action Agent

Give it a goal in plain English; it **plans, searches the web, reads pages, reflects on its own work, and takes actions** — pausing for your approval before anything that writes to the real world, with the final answer **streaming in as clean markdown with the sources it actually consulted**. Every run is durably recorded: **a paused approval survives a backend restart**, and every past run — finished or not — is browsable and replayable from a **run history** panel. Built on **free models only** (Groq `gpt-oss-120b` → Gemini Flash fallback).

TaskPilot is the real thing: an explicit **LangGraph** state machine with a **critic/self-correction** node, a **hard step limit**, a **human-in-the-loop approval gate**, **durable checkpointing** (SQLite-backed, not in-memory), and a **custom MCP server I built from scratch** — now with a **persistent session** — as the agent's action surface.

---

## What it proves
Agentic orchestration, tool use, the **Model Context Protocol**, human-in-the-loop control, durable state, and streaming UX — the skills behind "AI engineer / agent" roles

---

## Architecture

```
┌────────────────┐               ┌──────────────── FastAPI backend ─────────────────┐
│ React + Vite    │  POST /run   │  LangGraph state machine (per-thread, DURABLY     │
│ agent trace     │ ────────────▶│  checkpointed — AsyncSqliteSaver, survives a      │
│  (token-        │  SSE trace   │  restart)                                        │
│   streamed      │◀──────────── │                                                   │
│   final answer  │              │    START → plan → reason ─┬─▶ act ─┐             │
│   + sources)    │  POST /resume│                            │        │             │
│ approval dialog │ ────────────▶│                  (write?)  ├─▶ approval ──interrupt│
│  (per-field     │              │                            │        │   (pause)    │
│   form)         │              │                            └─▶ critic ─▶ finalize ─▶ END
│ run history     │  GET /runs   │                                        (streamed)  │
│  (browse +      │ ────────────▶│                                                   │
│   replay)       │              │   Tools:                                          │
│ tasks panel     │              │     • web_search  (DuckDuckGo, no key)            │
└────────────────┘               │     • read_url    (httpx + BeautifulSoup)         │
                                 │     • add_task / list_tasks / complete_task       │
                                 │         └── via CUSTOM MCP SERVER, ONE persistent  │
                                 │             stdio session for the process's life ──┼──▶ mcp_server/server.py
                                 │                                                   │      (FastMCP + SQLite,
                                 │   LLM: shared/llm.py  Groq gpt-oss-120b → Gemini   │       annotation-tagged
                                 │   Tracing: Langfuse on every LLM call (router);    │       read-only vs write)
                                 │            full agent trace streamed live to UI    │
                                 │   Run history: every event persisted to agent.db   │
                                 │            (runs_store.py) as it streams           │
                                 └────────────────────────────────────────────────────┘
```

### The agent loop (`backend/app/agent/graph.py`)
- **plan** — the LLM writes a short plan from the task + available tools.
- **reason** — ReAct-style: emit a `thought`, then either call **one** tool or give the final answer (strict JSON, robustly parsed).
- **act** — run the tool (in a thread, with a timeout); the result becomes an `observation`.
- **approval** — if the chosen tool is a **write** action, the graph **`interrupt()`s** and waits; the UI shows a per-field Approve/Reject dialog generated from the tool's MCP schema; `/resume` continues via `Command(resume=…)` — even after a full backend restart, because the checkpoint is on disk, not in memory.
- **critic** — judges the draft answer against the task and can bounce it back once for revision (self-correction).
- **finalize** — re-renders the critic-accepted draft as **streamed markdown**, token by token, and attaches a **derived source list** (pages actually read, queries actually run) — see [Decisions](#decisions--trade-offs) for why sources are derived rather than forced `[n]` citations.
- **safety rails** — a hard `max_steps` cap forces a final answer instead of looping forever; rejected actions are recorded so the agent doesn't retry them.

Every transition is emitted as a custom event, **persisted to the run history store** as it streams, and shown live in the UI's **agent trace**.

### The custom MCP server (`backend/mcp_server/`)
Instead of hard-coding tools into the agent, TaskPilot exposes a real **Model Context Protocol** server (built with the MCP Python SDK's `FastMCP`) over stdio. It offers a small task tracker backed by SQLite: `add_task`, `list_tasks`, `complete_task`. The agent is an **MCP client** ([`app/tools/mcp_client.py`](backend/app/tools/mcp_client.py)) — written by hand, not via an adapter library — that spawns the server once and keeps **one persistent session** open for the API process's lifetime (a supervisor-task pattern), running the initialize handshake, discovering tools dynamically, and calling them. The same server would work in Claude Desktop, Cursor, or any MCP host. Write-vs-read is now **annotation-derived** (`readOnlyHint` on each `@mcp.tool()`, fail-safe: no annotation ⇒ treated as write) rather than a hardcoded name list — `add_task`/`complete_task` are what the approval gate protects.

---

## Evaluation

A 24-task suite ([`backend/eval/dataset.json`](backend/eval/dataset.json)) spanning **research**, **deep research** (search → read), **action** (write, approval-gated), **mixed**, and **robustness** (bad URL, an impossible action with no matching tool, and a tool-error recovery case) tasks. It scores:
- **Tool-selection accuracy** — of the tools a task should use, how many the agent actually called (deterministic, from the trace).
- **Task success** — whether the final answer accomplishes the task, judged by a free Gemini Flash LLM-as-judge.
- **Operational health** — avg tool calls/task, error-event rate, approval-gate trigger rate.

The harness drives the real graph and **auto-approves** write actions (it plays the human), then writes a metrics table + per-task breakdown to [`eval/results.md`](backend/eval/results.md):

| Metric | Meaning | Real run (24 tasks) |
|--------|---------|---:|
| Task success | % of tasks the LLM judge says were accomplished | 54.2% |
| Tool-selection accuracy | % of expected tools actually used | 93.8% |
| Avg tool calls / task | efficiency / runaway check | 2.0 |
| Error-event rate | % of tasks that hit a handled error | 0.0% |
| Approval-gate rate | % of tasks that paused for approval | 37.5% |

```bash
cd backend
python eval/run_eval.py            # full suite → eval/results.md
python eval/run_eval.py --limit 5  # quick subset
```
> Agent evals **must** call a live LLM (and the web), so the numbers are generated on your machine from your free keys — they aren't pre-baked into the repo. The harness, dataset, scoring, and report are all here; one command fills in `eval/results.md`.

**Reading the gap honestly:** tool-selection accuracy (93.8%) shows the agent almost always reaches for the right tools; task success (54.2%) is markedly lower. Since the trajectory-level metric is strong, the shortfall most likely sits in *answer quality* under a strict LLM-judge bar rather than in orchestration — this harness doesn't currently log the judge's reasoning per task to confirm that, which is the natural next instrumentation step. One specific finding from the per-task table ([`eval/results.md`](backend/eval/results.md)) is worth calling out: on the "delete all my tasks" robustness case (no delete tool exists), the agent didn't hallucinate one — it called `complete_task` once per existing task, a real substitution using only tools that actually exist. It's scored `success: false` (marking isn't deleting), which is correct, but the *behavior* is exactly what that test case was probing for. (It also exposed a blind spot in the harness itself: `tool_score()` returns 100% whenever a task's `expected_tools` is empty, regardless of what was actually called — worth knowing before reading that column at face value for such a case. See GUIDE.md §6 for the full account.)

### Tests
A focused `pytest` suite ([`backend/tests/`](backend/tests)) covers the JSON-action parser, the MCP server↔client round-trip (incl. annotation-derived write flags), a full scripted agent run (plan → approval interrupt → resume → MCP write → critic → **finalize** → final), the derived-sources logic, and a `runs_store` round-trip — **no API key needed** (the LLM *and* the streaming call are stubbed):
```bash
cd backend
uv pip install -r requirements-dev.txt   # pytest + pytest-asyncio
python -m pytest -q                       # 10 passed
```

---

## Run it locally

> **Self-contained.** The LLM router + tracing are vendored under `backend/shared/`, and the MCP server is bundled — TaskPilot needs nothing outside `2-taskpilot/`. No database service to run (SQLite is a file).

**Prerequisites:** Python 3.11, Node 20+, and free keys. Copy `.env.example` → `.env` here and add `GROQ_API_KEY` + `GEMINI_API_KEY`.

```bash
# 1. Backend (also launches the MCP server as a subprocess on demand)
cd 2-taskpilot/backend
uv venv .venv && uv pip install -r requirements.txt
./.venv/Scripts/uvicorn app.api.main:app --reload --port 8001     # http://localhost:8001/docs

# 2. Frontend (new terminal)
cd ../frontend
npm install
npm run dev                                                        # http://localhost:5173
```

Type a task (or click a sample). Watch it plan and act in real time, with the final answer streaming in as markdown; when it wants to create a task it pauses for your approval — a per-field dialog generated from the tool's schema — approve and see it appear in the **Tasks created** panel. Open **Runs** in the header to browse and replay any past run, including resuming a paused one (works even after restarting the backend — the checkpoint is on disk).

**Try the MCP server standalone:**
```bash
cd backend && python mcp_server/server.py    # speaks MCP over stdio (Ctrl-C to stop)
```

**Durability demo, end to end:** start a write-action task → approve gate appears → **kill and restart `uvicorn`** → open the paused run from the **Runs** menu → click **Resume** → it completes exactly as if the server had never restarted.



## API
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/run` | Start a task; streams the agent trace as SSE (incl. streamed final-answer tokens) |
| POST | `/resume` | Approve/reject a paused write action; streams the rest — works after a restart |
| GET | `/runs` | List past runs (status, task, elapsed, preview) |
| GET | `/runs/{id}` | Full persisted event history for one run (replay) |
| DELETE | `/runs/{id}` | Remove a run from history |
| GET | `/tasks` | Tasks the agent created (via MCP) |
| GET | `/health` | Lists discovered tools (incl. MCP) |
