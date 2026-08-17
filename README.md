# TaskPilot — Autonomous Research & Action Agent

Give it a goal in plain English; it **plans, searches the web, reads pages, reflects on its own work, and takes actions** — pausing for your approval before anything that writes to the real world, with the final answer **streaming in as clean markdown with the sources it actually consulted**. Every run is durably recorded: **a paused approval survives a backend restart**, and every past run — finished or not — is browsable and replayable from a **run history** panel. Built on **free models only** (Groq `gpt-oss-120b` → Gemini Flash fallback).

> **Why this project exists:** agentic systems are where GenAI hiring is heading, and most "agent" demos are a single LLM call in a loop with no guardrails and no memory. TaskPilot is the real thing: an explicit **LangGraph** state machine with a **critic/self-correction** node, a **hard step limit**, a **human-in-the-loop approval gate**, **durable checkpointing** (SQLite-backed, not in-memory), and a **custom MCP server I built from scratch** — now with a **persistent session** — as the agent's action surface.

---

## What it proves
Agentic orchestration, tool use, the **Model Context Protocol**, human-in-the-loop control, durable state, and streaming UX — the skills behind "AI engineer / agent" roles.

**Resume bullet:**
> Built an autonomous LangGraph agent (plan→act→observe→reflect→finalize with a critic node, step limits, and a human-approval gate) that researches via web search + URL reading and takes actions through a **custom MCP server** with a persistent session; durable SQLite checkpointing means a paused approval survives a backend restart; streamed markdown final answers with derived sources, a browsable/replayable run history, a 24-task eval scoring tool-selection and task-success, Groq→Gemini failover — all free-tier.

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

## Decisions & trade-offs

- **LangGraph state machine over a free-form ReAct loop.** An explicit graph makes every step inspectable, streamable, resumable, and testable — and gives first-class human-in-the-loop via `interrupt()`/checkpointing. Cost: more wiring than "while True: call_llm()". Worth it for control and the live trace.
- **Durable `AsyncSqliteSaver` over in-memory `MemorySaver`.** A paused approval used to vanish if the backend restarted. The checkpoint now lives in `agent.db`, so `/resume` works even after a full restart — verified live by pausing a run, killing the server, restarting it, and resuming successfully. Cost: a shared-SQLite-file lock-contention edge case (see hard parts) that needed a non-blocking-write + retry fix.
- **Run history is server-side (`runs_store.py`), not client-side localStorage.** The backend already owns the authoritative event stream *and* the resumable checkpoint keyed by the same `thread_id` — a client-side history would desync from what's actually resumable the moment a paused run outlives the browser tab.
- **Sources are derived from the scratchpad, not forced `[n]` citation markers.** The final answer is produced by whichever of three free-tier models is live, inside a strict-JSON decision protocol; layering a citation format on top of that would make weaker fallback models drop the JSON shape. A deterministic list of "pages actually read / queries actually run" is honest and can't hallucinate a source.
- **A dedicated `finalize` node for streaming**, rather than streaming the reason node's answer directly. `reason`'s `final_answer` is one field inside a JSON object — unstreamable, and shaped by a strict-output prompt that fights natural prose. `finalize` re-renders the critic-accepted draft with one more call, this time using the real streaming API, falling back to the draft verbatim on any stream failure.
- **Persistent MCP session over a fresh subprocess per call.** The original per-call spawn was simple but paid ~1-2s per tool call and — observed live while building this — could hang for 60+ seconds under load, which held the checkpoint write lock open and caused flaky `sqlite3` errors elsewhere. A supervisor-task-owned session removes both.
- **Write-tool detection via MCP annotations, not a hardcoded name set.** `readOnlyHint` on each `@mcp.tool()` is the single source of truth; a tool with no annotation defaults to **write** (fail-safe — a new tool is gated by default, not silently exempt).
- **Built the MCP server + client myself (no `langchain-mcp-adapters`).** The point is to *demonstrate* I understand MCP — the handshake, tool discovery, stdio transport, annotations — not hide it behind a wrapper.
- **Approval emitted from the interrupt payload, not the node.** On resume, the approval node re-runs, so emitting the prompt inside it would duplicate it. The API emits `approval_required` from the interrupt payload exactly once per pause.
- **Tools never raise into the agent.** Bad URLs, timeouts, rate limits, junk args all return a readable error *string* the agent treats as an observation and adapts to — robustness is the portfolio signal, not happy-path demos.
- **Hard step limit + single critic pass.** Runaway loops and infinite self-critique are the classic agent failure modes; both are explicitly bounded.
- **Groq primary → Gemini fallback** via the vendored [`shared/llm.py`](backend/shared/llm.py), so a Groq 429 mid-task doesn't kill the run.

## Known limitations
- Web search/reading hit the live internet, so research answers vary run to run (eval uses an LLM judge, not string matching).
- SQLite task store is single-file/single-tenant; no auth on the API (add before any public deploy).
- The agent does single-tool-per-step ReAct; parallel tool calls aren't used.
- "Stop" works by disconnecting the SSE stream (the server-side generator is cancelled and the run is marked `stopped`) — there's no separate cancel endpoint, and any in-flight tool call still runs to completion server-side before the cancellation is observed.
- Two SQLite writers share `agent.db` (the LangGraph checkpointer and the run-history recorder); under a very slow tool call this can occasionally still miss persisting a single trace event after retries — the live run is unaffected, only that one replay entry is missing.

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

## Deploy (free tier)
- **Backend** → Render / HF Spaces via [`backend/Dockerfile`](backend/Dockerfile) (`docker build -t taskpilot-api .` from `backend/`). The MCP server runs as a child process inside the same container. Mount a persistent volume for `agent.db`/`tasks.db` if the platform's filesystem doesn't survive restarts, or the durability story only holds within one container's lifetime.
- **Frontend** → Vercel; set `VITE_API_BASE` to the backend URL; add the Vercel origin to `CORS_ORIGINS`.
- **Tracing** → set `LANGFUSE_*` to trace the agent's LLM calls (latency, tokens, cost) in Langfuse; the full plan→act→observe→finalize trace also streams live in the UI and is persisted to run history.

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
