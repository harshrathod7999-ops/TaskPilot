# TaskPilot — The Complete Build & Mastery Guide

> A teaching guide to the autonomous agent you built: how the LangGraph state machine, the human-approval interrupt, and the hand-built MCP server actually work, how to evaluate and deploy it, a full debugging playbook, and research-backed ways to push it further and **make it uniquely yours**.
>
> Re-verified against the live landscape in **June–July 2026** (sources at the bottom). The agent stack moves fast — re-check the LangGraph, MCP, and provider docs before relying on a version or model ID.

---

## 1. What we built & why it matters

**TaskPilot** is an autonomous research-and-action agent. You give it a goal in plain English; it **plans, searches the web, reads pages, reflects on its own answer, and takes actions** — pausing for your approval before anything that writes to the real world, with the answer **streaming in as markdown** alongside the sources it actually consulted. You watch the entire reasoning trace stream live, and every run — finished, errored, or still paused — is **durably recorded and replayable** from a run-history panel.

What makes it portfolio-grade rather than a toy: it's an **explicit LangGraph state machine** (not a black-box "agent that loops"), with a **critic/self-correction** node, a **hard step limit**, a **human-in-the-loop approval gate** built on LangGraph's `interrupt()` backed by **durable SQLite checkpointing** (not in-memory — a paused run survives a backend restart), and a **custom Model Context Protocol (MCP) server I built from scratch**, now with a **persistent session**, as the agent's action surface.

**Why it matters for hiring:** agentic systems are where GenAI hiring is moving, and the two hardest-to-fake skills are **eval rigor** and **production failure handling** — exactly what TaskPilot foregrounds. In 2026, "fully autonomous" is still a fantasy in production; real deployments demand human oversight on consequential actions *and* durable state that survives restarts — both are demonstrated here, not just claimed.

**Resume bullet:**
> Built an autonomous LangGraph agent (plan→act→observe→reflect→finalize with a critic node, hard step limits, and a human-approval gate via `interrupt()`) that researches with web search + URL reading and acts through a **custom MCP server** with a persistent session; durable SQLite checkpointing means a paused approval survives a backend restart; streamed markdown final answers with derived sources, a replayable run-history UI, a 24-task eval scoring tool-selection + task-success, a stubbed-LLM/stubbed-stream test suite, and Groq→Gemini failover — all on free-tier models.

---

## 2. Prerequisites

**Accounts / keys (all free):** Groq (https://console.groq.com/keys) for the agent's reasoning, Google AI Studio (https://aistudio.google.com/app/apikey) for fallback + the eval judge, and optionally Langfuse for tracing.

**Set up your keys:** the repo ships only `.env.example` (a template with no real values) — `.env` itself is gitignored and never committed, so copy it once locally:
```bash
cd 2-taskpilot
cp .env.example .env    # then edit .env and paste in your GROQ_API_KEY / GEMINI_API_KEY
```
Both projects' backends load `.env` from their own project folder (not a shared repo root), so each is self-contained — this is also why a stray `.env` accidentally left at the repo root has no effect on either project and is safe to delete.

**Tools:** Python 3.11, Node 20+, `uv`. No database server — the MCP server uses SQLite (a file). No Docker needed to run locally.

**Assumed knowledge:** Python (incl. `async`/`await`), a little React/TypeScript, and the *idea* of an LLM "agent" (a model that picks tools in a loop). Everything else is explained.

---

## 3. Architecture

```
┌────────────────┐               ┌──────────────── FastAPI backend ─────────────────┐
│ React + Vite    │  POST /run   │  LangGraph state machine (per-thread, DURABLY     │
│ agent trace     │ ────────────▶│  checkpointed via AsyncSqliteSaver — survives a   │
│ (streamed final │  SSE trace   │  backend restart)                                 │
│  answer + srcs) │◀──────────── │                                                   │
│ approval dialog │              │     START → plan → reason ─┬─▶ act ─┐             │
│ (per-field form)│  POST /resume│                            │        │             │
│ run history     │ ────────────▶│                  (write?)  ├─▶ approval ──interrupt│
│ (browse+replay) │              │                            │        │   (PAUSE)    │
│ tasks panel     │  GET /runs   │                            └─▶ critic ─▶ finalize ─▶ END
└────────────────┘ ────────────▶│                                        (streamed)  │
                                 │   Tools:                                          │
                                 │     • web_search (DuckDuckGo) • read_url (httpx)   │
                                 │     • add_task / list_tasks / complete_task ───────┼─▶ mcp_server/server.py
                                 │         via CUSTOM MCP SERVER, ONE persistent      │     (FastMCP + SQLite,
                                 │         stdio session for the whole process life   │      annotation-tagged)
                                 │   LLM: shared/llm.py  Groq gpt-oss-120b → Gemini   │
                                 │   Run history: every event persisted as it streams │
                                 │            (runs_store.py, same agent.db file)     │
                                 └────────────────────────────────────────────────────┘
```

### The agent loop (control flow)
1. **plan** — the LLM writes a short plan from the task + the available tools.
2. **reason** — ReAct-style: emit a `thought`, then return **strict JSON** that is either *one* tool call or a `final_answer`.
3. **route** — if it's a final answer → **critic**; if the chosen tool is a **write** action → **approval**; otherwise → **act**.
4. **act** — run the tool (in a thread, with a timeout); the result becomes an `observation`; loop back to **reason** with `step += 1`.
5. **approval** — for write actions, the graph **`interrupt()`s** and waits, carrying the tool's thought/step/schema. The UI renders a per-field form from the schema. `/resume` continues with `Command(resume=…)` — even after a full restart, since the checkpoint is on disk.
6. **critic** — judges the draft answer; can bounce it back **once** for revision (self-correction), then routes to **finalize** instead of ending directly.
7. **finalize** — re-renders the accepted draft as **streamed markdown** (real token-by-token generation, not one block) and attaches a **derived source list**; falls back to the draft verbatim on any stream error.
8. **safety rails** — a hard `max_steps` cap composes a final answer instead of looping forever; rejected actions are recorded so the agent won't retry them.

Every transition is emitted as a custom event, **persisted to run history as it streams**, and shown live in the browser as the **agent trace**.

### The custom MCP server (the differentiator)
Instead of hard-coding tools into the agent, TaskPilot exposes a real **Model Context Protocol** server (built with the MCP Python SDK's `FastMCP`) over stdio. It offers a task tracker backed by SQLite. The agent is a **hand-written MCP client** that spawns the server **once** and keeps **one persistent session** alive for the API process's whole life (a supervisor-task pattern — see §4.9), running the `initialize` handshake, discovering tools dynamically, and calling them — the same server would plug into Claude Desktop, Cursor, or any MCP host. Write-vs-read is **annotation-derived** (`readOnlyHint` on each tool) rather than a hardcoded name list; `add_task`/`complete_task` are what the approval gate protects.

---

## 4. Step-by-step build (mirrors the real files)

### 4.1 Config — `app/config.py`
Env-driven settings with the safety rails as first-class knobs: `AGENT_MAX_STEPS` (loop cap), `AGENT_MAX_CRITIQUES`, `TOOL_TIMEOUT_SECONDS`. `.env` loads from the **project folder** so the project runs standalone as a zip.

### 4.2 The MCP server — `mcp_server/server.py` + `db.py`
```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("taskpilot-tools")

@mcp.tool()
def add_task(title: str, notes: str = "", due: str = "") -> dict:
    """Create a new task in the tracker."""
    return _add_task(title, notes, due)   # writes to SQLite

if __name__ == "__main__":
    mcp.run()   # stdio transport
```
That's a complete, spec-compliant MCP server. `@mcp.tool()` turns a typed Python function into a discoverable tool with an auto-generated JSON schema.

### 4.3 The MCP client — `app/tools/mcp_client.py`
We use the **raw** MCP SDK (not an adapter) so the integration is visible: spawn the server, handshake, list/call tools. A short-lived session per call keeps lifecycle simple inside the async loop.
```python
async def call_mcp_tool(name, args):
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()                 # the MCP handshake
            result = await session.call_tool(name, args or {})
            return _content_to_text(result)
```

### 4.4 The tool registry — `app/agent/tools_registry.py`
Local tools (`web_search`, `read_url`) and the MCP-discovered tools are normalised into one `Tool` shape: `name`, `description`, `args_hint`, **`write`** (the flag the approval gate keys off), **`schema`** (the raw MCP JSON Schema, `None` for local tools — powers the approval dialog's per-field form), and an async `run`. Tools **never raise into the agent** — bad URLs, timeouts, and rate-limits return a readable error *string* the agent treats as an observation and adapts to.

### 4.5 The graph — `app/agent/graph.py`
The nodes are pure-ish async functions over `AgentState`. Two pieces worth studying:

**The ReAct decision + a robustness retry:**
```python
raw = await _llm(reason_user(state, registry), max_tokens=900)
decision = parse_json(raw)
if not decision:                       # model returned prose, not JSON
    raw = await _llm(messages + [correction], max_tokens=900)   # one corrective retry
    decision = parse_json(raw)
```

**The human-approval interrupt** (this follows the documented LangGraph HITL rules exactly):
```python
async def approval_node(state):
    pending = state["pending"]
    tool_def = registry.get(pending["tool"])
    # NOTE: no _emit() here. Code before interrupt() re-runs on resume, so we
    # emit `approval_required` from the API (off the interrupt payload) once.
    # The extra fields (thought/step/schema) are what let the UI render a
    # per-field edit form instead of a raw JSON textarea.
    decision = interrupt({
        "tool": pending["tool"], "args": pending["args"], "thought": pending.get("thought"),
        "step": state["step"], "max_steps": state["max_steps"],
        "args_hint": tool_def.args_hint if tool_def else None,
        "schema": tool_def.schema if tool_def else None,
    })
    ...
```
Why no `try/except` around `interrupt()`, why a JSON-safe value, and why nothing with side-effects before it: on resume LangGraph **re-executes the node from the top** until the interrupt returns — so pre-interrupt code must be idempotent. These are the official rules, and following them is a strong interview talking point.

**The graph wiring + checkpointer:**
```python
g.add_conditional_edges("reason", route_after_reason, ["act", "approval", "critic"])
g.add_conditional_edges("approval", route_after_approval, ["act", "reason"])
g.add_edge("act", "reason")
g.add_conditional_edges("critic", route_after_critic, ["reason", "finalize"])  # not END anymore
g.add_edge("finalize", END)
return g.compile(checkpointer=checkpointer)   # AsyncSqliteSaver in prod, MemorySaver in tests/eval
```
`build_graph()` now takes the checkpointer as a parameter rather than hardcoding `MemorySaver()` — tests and the eval harness pass a fast in-memory one, while `get_graph()` (§4.8) builds the durable one the API actually runs on.

### 4.6 The API — `app/api/main.py`
`/run` streams the trace as SSE (`stream_mode=["custom","updates"]`): custom events become trace frames; when an `__interrupt__` appears, the API emits `approval_required` (from the interrupt payload) then `paused`, and closes the stream. `/resume` continues with `Command(resume={...})` on the same `thread_id` — and now this works **even after a backend restart** (§4.8). Every non-transient event is also persisted to run history as it streams (§4.9).

### 4.7 The frontend — `frontend/src/`
Full **shadcn/ui** (dialog, alert-dialog, dropdown-menu, sonner, skeleton, sheet — hand-wired into the existing design tokens, not run through `shadcn init`) with working dark mode. A streaming **agent-trace timeline** (icons per step type) renders the final answer as **markdown** with derived **source chips**, appending real tokens as they arrive. The approval dialog is now an **AlertDialog** rendering a **per-field form** generated from the tool's JSON Schema (string/number/boolean inputs, JSON fallback for nested values) instead of one big JSON textarea. A **Runs** menu in the header lists persisted history; selecting one replays its trace statically, and a paused run offers a **Resume** button that rehydrates the live approval flow. The SSE-over-POST reader is hand-rolled because `EventSource` can't POST.

### 4.8 Durable checkpointing — `MemorySaver` → `AsyncSqliteSaver`
The original checkpointer was `MemorySaver()` — fine until the backend restarts, at which point every paused approval simply vanishes. The fix (`app/agent/graph.py`):
```python
async def get_graph():
    global _graph, _agent_conn
    async with _graph_lock:
        if _graph is None:
            _agent_conn = await aiosqlite.connect(get_settings().agent_db_path, timeout=10)
            await _agent_conn.execute("PRAGMA journal_mode=WAL")
            saver = AsyncSqliteSaver(_agent_conn)
            await saver.setup()
            _graph = build_graph(saver)
    return _graph
```
A **plain long-lived `aiosqlite` connection**, not `AsyncSqliteSaver.from_conn_string()`'s context-manager form — that form ties the connection's cancel scope to whichever task opens it, which breaks the moment a *different* request task uses the saver. A bare connection has no such constraint; it's closed once, explicitly, in the FastAPI lifespan's shutdown. The payoff: pause a run for approval, kill and restart `uvicorn`, and `/resume` still works — the checkpoint was on disk the whole time.

### 4.9 Run history — `app/runs_store.py`
A durable checkpoint means a paused thread can be resumed later, but there was still no way to *find* it, or to look back at a finished run's trace. `runs_store.py` (plain `sqlite3`, same style as `mcp_server/db.py`) adds two tables — `runs` (status, task, timing) and `run_events` (every SSE frame, in order) — in the **same `agent.db` file** as the checkpointer. `api/main.py`'s `_stream_graph` is the single recording point: every frame it yields is also persisted (skipping `status`/`final_token` — noise and superseded-by-the-final-frame, respectively), and a `try/except asyncio.CancelledError` ensures a client disconnect (Stop button, tab close) marks the run `stopped` rather than leaving it dangling as `running` forever.

**Why server-side, not the client-side `localStorage` pattern used in the other portfolio projects:** the backend already owns the authoritative event stream *and* the resumable checkpoint keyed by the same `thread_id` — a browser-side history would immediately desync the moment a paused run outlived the tab that started it.

### 4.10 Streaming the final answer — the `finalize` node
`reason`'s `final_answer` is one field inside a strict-JSON decision object — there's no way to stream a single JSON field token-by-token, and the JSON-output constraint actively fights natural markdown formatting. `finalize_node` solves both: it re-renders the critic-accepted draft with one more LLM call, this time via the real streaming API, bridged from a worker thread into the async graph:
```python
def produce():
    for tok in chat_stream(messages, model=s.primary_model, fallback_model=s.fallback_model):
        loop.call_soon_threadsafe(queue.put_nowait, tok)   # only touches the queue
    loop.call_soon_threadsafe(queue.put_nowait, _DONE)

threading.Thread(target=produce, daemon=True).start()
while True:
    item = await queue.get()
    if item is _DONE: break
    _emit("final_token", text=item)   # _emit (the stream writer) only ever called from THIS task
```
The producer thread only ever pushes onto the queue; the node's own async loop drains it and calls `_emit`, because LangGraph's stream writer is only valid on the task that owns the node. On any stream failure, `finalize` falls back to the draft **verbatim** — a presentation step must never fail a run whose answer was already accepted by the critic.

### 4.11 Persistent MCP session + annotation-derived write flags
The original `mcp_client.py` spawned a **fresh subprocess for every single tool call** — simple, but it paid a 1-2s spawn cost every time, and while building this project it was caught **hanging for over a minute** under load (see §5), which held the checkpoint write lock open and produced flaky `sqlite3` errors elsewhere in the app. The rewrite (`_MCPSession`) is a supervisor-task pattern: one task enters the `stdio_client`/`ClientSession` context managers and holds them open for the API process's entire life, blocking on a shutdown event; every other task just calls `session.call_tool()`, which `ClientSession` safely multiplexes over the one connection. A failed call triggers one restart-and-retry before giving up.

Write-tool detection also moved off a hardcoded `{"add_task", "complete_task"}` set and onto **MCP tool annotations** — `mcp_server/server.py` tags each tool with `ToolAnnotations(readOnlyHint=...)`, and the client derives `write = not (annotations and annotations.readOnlyHint)`. A tool with **no** annotations defaults to `write=True` — fail-safe, so a newly added tool is gated by default instead of silently bypassing human approval.

---

## 5. The hard parts & how we solved them

| Hard part | The trap | The fix |
|-----------|----------|---------|
| **Pausing across two HTTP requests** | `/run` ends at the approval; `/resume` is a *new* request — how does the graph remember where it was? | LangGraph `interrupt()` + a **checkpointer** keyed by `thread_id`; `/resume` sends `Command(resume=…)` to the same thread. |
| **Duplicate approval prompt on resume** | The approval node re-runs from the top on resume, re-emitting any event before `interrupt()`. | Emit `approval_required` from the **API** off the interrupt payload — exactly once per pause — not inside the node. |
| **Model returns prose, not JSON** | A non-JSON ramble silently became the "final answer," truncating the task. | A **single corrective retry** ("reply with ONLY the JSON object") before falling back. |
| **Runaway loops** | Agents loop forever or self-critique infinitely. | Hard `max_steps` (compose a final answer instead of looping) + a single critic pass (`max_critiques`). |
| **Tools that crash the agent** | A 404, a timeout, or junk args throws and kills the run. | Every tool returns an **error string**, wrapped in `asyncio.wait_for` timeouts; the agent observes and adapts. |
| **Rate limits (429)** | Groq free tier is ~30 RPM. | Groq→Gemini failover + backoff in the vendored router. |
| **A slow tool call held the checkpoint write lock for 60+ seconds** | Live-testing an MCP tool call and a DuckDuckGo search each hung for ~67s (the old per-call MCP subprocess spawn, and DDGS being slow). The `AsyncSqliteSaver`'s write transaction stayed open for that whole span, and `runs_store`'s synchronous `sqlite3` writer on the *same file* raised `database is locked` — and because it ran on the FastAPI event loop, retrying it there would have stalled every other request for the same duration. | Two independent fixes: (1) moved every `runs_store` call behind `asyncio.to_thread` so contention costs a worker thread, never the event loop — verified by polling `/health` during a stuck run and seeing zero latency spikes; (2) the persistent MCP session (§4.11) removes the actual hang, since one long-lived subprocess doesn't have the per-call spawn's failure mode. |
| **The new test suite would silently start making live LLM calls** | `finalize_node` calls `chat_stream` directly, bypassing the `_llm` wrapper the existing tests already stub — the "no API key required" test suite would have quietly started hitting the real Groq/Gemini APIs the moment streaming was added. | Caught by re-running the suite after adding `finalize`; stubbed `chat_stream` alongside `_llm` in both tests that reach the node. A test suite claiming "no key needed" is a contract — verify it after every change that touches the LLM call path, not just at the time it was written. |
| **The MCP client singleton broke across test-suite event loops** | `_MCPSession`'s `asyncio.Event`/`Lock` objects were created once in `__init__`, bound to whichever event loop existed at that moment. `pytest-asyncio`'s default `function`-scoped loop gives each test a *fresh* loop, so a later test reusing the module-level singleton crashed with "bound to a different event loop." | `_rebind_if_new_loop()` checks `asyncio.get_running_loop()` against a stored reference on every entry point and recreates the primitives (dropping any stale supervisor task, which belongs to an already-closed loop anyway) when it's changed. In production there's exactly one loop for the app's life, so this never actually fires there — it exists purely for test isolation. |

---

## 6. Evaluation & results

Agent eval is harder than LLM eval: a single trajectory hides a planner, a tool selector, retries, and a final answer that may or may not satisfy the goal. Crucially, **agents judged only on final output pass 20–40% more cases than step-level evaluation reveals** — so we score the *trajectory*, not just the answer.

TaskPilot's harness ([`backend/eval/`](backend/eval)) runs the real graph on 24 tasks (auto-approving writes — it plays the human) and reports:
- **Tool-selection accuracy** — did it call the tools the task needs? (a trajectory metric, deterministic from the trace)
- **Task success** — did the final answer accomplish the goal? (free Gemini Flash LLM-as-judge)
- **Operational health** — avg tool calls/task, error-event rate, approval-gate rate.

The set includes 3 **robustness** cases beyond the happy path: a dead URL (`read_url` must fail gracefully, not hallucinate content), an impossible action ("delete all my tasks" — no delete tool exists), and a tool-error recovery case (mark a nonexistent task ID complete, observe the MCP error, report it honestly).

```bash
cd backend && python eval/run_eval.py        # writes eval/results.md (needs free keys)
```
> Agent evals must call a live LLM + the web, so numbers are generated on your machine, not pre-baked.

**A real run (24 tasks):**

| Metric | Score |
|--------|------:|
| Task success (LLM-judged) | 54.2% |
| Tool-selection accuracy | 93.8% |
| Avg tool calls / task | 2.0 |
| Error-event rate | 0.0% |
| Approval-gate rate | 37.5% |

Full per-task breakdown is committed at [`backend/eval/results.md`](backend/eval/results.md). Two things worth reading honestly rather than smoothing over:

**The success/tool-selection gap is real and unexplained by this harness alone.** Tool-selection accuracy (93.8%) shows the agent almost always picks the right tools; task success (54.2%) is markedly lower. Since the trace-level metric is strong, the gap most likely sits in answer *quality* — free-tier model output on a strict LLM-judge bar, or the judge itself being stricter/less consistent than a human — not in tool selection or orchestration. This harness doesn't currently inspect the answer text to distinguish those causes; that's the natural next instrumentation step (log the judge's `reason` field per task, not just the boolean).

**The "delete all my tasks" robustness case (t23) revealed a real limitation in the scoring formula, not just in the agent.** `tool_score()` returns `1.0` whenever `expected_tools` is empty — `if not expected: return 1.0` — regardless of what the agent actually did. I'd assumed (before running the eval) that an empty `expected_tools` would validate "the agent correctly called nothing" for an impossible action; it doesn't — the 100% shown for that row is vacuous, not earned. What the agent *actually* did was more interesting than either outcome I'd planned for: with no delete tool available, it called `complete_task` once per existing task in the tracker — a real, sensible substitution for "get rid of my tasks" using only tools that actually exist, rather than hallucinating a `delete_task` call. The LLM judge correctly scored this `success: false` (marking isn't deleting), but the behavior itself — reaching for a legitimate approximation instead of a fabricated capability — is exactly the failure mode this test case was designed to probe, and the per-task `tools_used` column in `results.md` is what actually shows it, not the aggregate score. Lesson: verify a new eval metric's edge-case formula against the actual harness code before writing about what it "will show" — I wrote the original version of this paragraph before running the eval, and the real run corrected it.

**Tests (no key needed)** — [`backend/tests/`](backend/tests) stubs the LLM *and* the streaming call, and covers the JSON-action parser, the MCP server↔client round-trip (incl. annotation-derived write flags), a **full scripted run** (plan → approval interrupt → resume → MCP write → critic → **finalize** → final), the derived-sources logic, and a `runs_store` round-trip:
```bash
cd backend && uv pip install -r requirements-dev.txt && python -m pytest -q   # 10 passed
```

---

## 7. Deployment (free tier)
- **Backend** → Render / HF Spaces via [`backend/Dockerfile`](backend/Dockerfile) (`docker build -t taskpilot-api .` from `backend/`). The MCP server runs as a child process inside the same container.
- **Frontend** → Vercel; set `VITE_API_BASE` to the backend URL; add the Vercel origin to `CORS_ORIGINS`.
- **State is durable now** — `AsyncSqliteSaver` writes to `agent.db` on disk, so a paused run survives a process restart. The remaining caveat: on a platform whose filesystem doesn't persist across deploys/restarts (e.g. an ephemeral container), mount a persistent volume for `agent.db`/`tasks.db`, or the durability story only holds within one instance's lifetime. A managed Postgres checkpointer would be the next step for true multi-instance durability (see §9).
- **Tracing** → set `LANGFUSE_*` to trace the agent's LLM calls.

---

## 8. Debugging playbook (find it → fix it)

### The agent / graph
- **`/health` shows `tools_ok: false` or MCP tools missing** → the MCP server failed to start. Confirm standalone: `python mcp_server/server.py` (should sit waiting on stdio; Ctrl-C to exit). Common causes: a syntax error in `server.py`/`db.py`, or `sys.executable` not resolving. The agent still runs with web tools only and logs a warning.
- **Run never pauses for approval** → the tool's MCP annotation says `readOnlyHint=True` when it shouldn't, or the tool has no annotation at all AND something upstream still treats it as safe. Check `_is_write_tool()` in `app/tools/mcp_client.py` and the tool's `@mcp.tool(annotations=...)` in `mcp_server/server.py`. Check the `action` event's tool name in the trace.
- **Approval dialog appears twice** → you're emitting `approval_required` inside `approval_node`. It must come from the API off the `__interrupt__` payload (code before `interrupt()` re-runs on resume).
- **`GraphRecursionError`** → too many node visits. Either the agent is looping (tighten the prompt / lower `max_steps`) or raise `recursion_limit` in the run config (default here is 60).
- **Agent ends immediately with a weird "final answer"** → the model returned non-JSON and the corrective retry also failed. Inspect the raw output; usually the reason prompt needs tightening or `max_tokens` raising.
- **State looks wrong after resume** → remember nodes re-run from the top on resume; never put non-idempotent side-effects before `interrupt()`. (LangChain ties ~60% of production agent incidents to state handling — treat state as the prime suspect.)
- **`/resume` fails with "no checkpoint found" after a restart** → confirm `agent.db` actually persisted (check its mtime) and that `AGENT_DB` points at the same file both before and after the restart — a relative path resolved from a different working directory will silently open a *different*, empty database.

### Tools & persistence
- **`web_search` returns "ERROR: …rate…"** → DuckDuckGo throttled you. Back off, or swap in Tavily/Gemini grounding. The agent already treats it as an observation and adapts.
- **`read_url` returns "not a readable text/HTML page"** → the URL is a PDF/binary or JS-rendered SPA. Add a PDF branch or a headless renderer if you need those.
- **`sqlite3.OperationalError: database is locked`** → two writers on `agent.db` (the checkpointer + `runs_store.py`) contended, most likely because a tool call is running unusually slowly (a slow web search, a stuck MCP call) and holding the checkpoint transaction open. `runs_store`'s writes already retry with backoff and run off the event loop (`asyncio.to_thread`), so the live run is never blocked — but if you see this a lot, look at *why* a tool call is taking so long first; the lock is a symptom, not the disease.
- **MCP tool call is slow or times out even after the persistent-session rewrite** → confirm only **one** "Processing request of type" startup line appears in the logs (proof the session is reused, not respawned); if calls are still slow, it's the underlying tool logic (SQLite contention in `mcp_server/db.py`, e.g.), not the transport.
- **A past run's replay is missing an event** → most likely that one event failed to persist after retries during unusually heavy lock contention (see above) — the live run itself was unaffected; this only touches history fidelity.

### API / frontend
- **Trace doesn't stream (appears all at once)** → a proxy is buffering SSE. The backend sends `X-Accel-Buffering: no`; disable buffering on any nginx/Cloudflare in front of `/run` and `/resume`.
- **Final answer doesn't stream token-by-token** → confirm `finalize_node` is actually being reached (look for a `status{phase:"finalizing"}` event) and that `chat_stream` isn't falling back to the non-streaming path; check for a warning log `[finalize] stream failed, falling back to draft`.
- **CORS error** → add your frontend origin to `CORS_ORIGINS` and restart.
- **`/run` errors instantly** → missing/invalid `GROQ_API_KEY` *and* `GEMINI_API_KEY`; the plan step's LLM call fails. The API turns it into a graceful `error` event.
- **Tests hang or fail with "bound to a different event loop"** → you're seeing the exact issue §5 documents for `_MCPSession`; make sure `_rebind_if_new_loop()` hasn't been removed/bypassed, and that any new test touching MCP calls `shutdown_mcp_session()` after changing `TASKPILOT_DB`.

### General technique
The trace **is** the debugger: read the `thought → action → observation` sequence. If the agent picked the wrong tool, fix the tool descriptions/`args_hint`; if it looped, tighten the finish criteria; if the answer is wrong but the observations were right, fix the reason/critic prompts.

---

## 9. Make it uniquely *yours* (research-backed stretch goals)

Pick 1–2. Each is a strong, current interview talking point.

> Since this list was written, two of its items shipped for real: **D's persistent checkpointer** (§4.8 — `AsyncSqliteSaver`, not Postgres, but the same durability property: a paused run survives a restart) and half of **C's MCP upgrade** (the persistent-session part, §4.11 — though it's still stdio, not the remote streamable-http transport). The remaining items below are still genuinely open.

### A. Re-skin the agent for a domain (highest ROI for "unique")
Swap the task-tracker MCP tool for a domain action and curate a real eval set:
- **DevOps copilot** — tools to query logs / open a (mock) incident; approval-gate the "page on-call" action.
- **Sales/CRM agent** — research a company, then draft + (on approval) "log" an outreach note.
- **Personal research assistant** — search + read + save a structured report artifact.
- **Code-fixing agent** — generate code, run it in a **Docker sandbox** against tests, read results, iterate until green. *The sandboxing alone is a great interview conversation* and is a top-recommended 2026 project.

### B. Go multi-agent (supervisor pattern)
Add a **supervisor** that routes between specialized sub-agents (a `researcher`, a `writer`, a `critic`), each with its own scratchpad. LangGraph supports hierarchical graphs in one framework. This is the canonical "intermediate" standout project for 2026.

### C. Finish the MCP layer upgrade to 2026-grade
- **Remote, stateless HTTP MCP server** (streamable-http transport) instead of stdio — it can run behind a plain load balancer and be cached, and would remove even the one-persistent-subprocess dependency the current design still has. Note this is a bigger change than it sounds: the approval-gate annotation logic (§4.11) and the `_MCPSession` supervisor pattern both carry over unchanged, only the transport swaps.
- **OAuth-protected MCP** following the 2026 spec (validate the `iss` param, dynamic client registration) — exactly how Stripe/Linear/Asana ship remote MCP.
- **Publish your server to the official MCP Registry** (the npm-style index that grew to ~2,000 servers) — a concrete, verifiable artifact recruiters can see.

### D. Further production state + observability
- **Postgres/Redis checkpointer** instead of SQLite, for true multi-instance durability (SQLite's one-file-one-writer model doesn't survive horizontal scaling — the current design assumes one API process).
- **LangSmith / AgentEvals** for **trajectory-level eval** — assert the agent took the *right steps and tool calls*, not just landed on the right answer. Add cost/latency and step-count to the report (the run-history store already captures timing per run — extending it to per-step timing is a small addition).

### E. Demo presentation (do this regardless)
Recruiters won't clone your repo — **record a 60–90s walkthrough** (or a GIF) of the live trace, an approval being granted, and (the strongest moment) killing the backend mid-pause and resuming from run history — and link it at the top of the README. A working demo link is worth 10× a repo alone, and consistent commits signal you're actively building.

---

## 10. How to talk about it in an interview

**Q: How does the human-in-the-loop pause actually work?**
> LangGraph's `interrupt()` plus a checkpointer. When the agent chooses a write tool, the node calls `interrupt(payload)`, which suspends the graph and persists state by `thread_id`. The UI approves, and a separate `/resume` request sends `Command(resume=decision)` to that thread. Key subtlety: the node re-runs from the top on resume, so I keep all side-effects *after* the interrupt and emit the approval prompt from the API off the interrupt payload — exactly once.

**Q: Why build your own MCP server instead of just functions?**
> To show I understand the protocol — discovery, the initialize handshake, stdio transport, tool schemas — and to make the action layer reusable by any MCP host, not just my agent. The same server works in Claude Desktop or Cursor unchanged.

**Q: Why LangGraph over a plain ReAct loop?**
> An explicit state machine makes every step inspectable, streamable, resumable, and testable, and gives first-class human-in-the-loop and checkpointing. A `while True: call_llm()` loop gives you none of that.

**Q: How do you evaluate an agent?**
> At the trajectory level, not just final output — final-only eval misses 20–40% of failures. I score tool-selection accuracy and step counts from the trace, plus LLM-judged task success and operational metrics like error rate and approval-gate rate.

**Q: What happens when a tool fails or the model misbehaves?**
> Tools never raise — they return error strings the agent observes and adapts to. Bad JSON gets one corrective retry. A hard step limit prevents runaway loops, and the critic bounds self-correction to one pass.

**Q: Why does the approval interrupt survive a backend restart?**
> The checkpointer moved from LangGraph's in-memory `MemorySaver` to `AsyncSqliteSaver`, writing to a real SQLite file. The graph state at the moment of `interrupt()` — including which thread paused and why — is on disk, not in process memory, so a fresh process can pick up `/resume` on the same `thread_id` exactly where the old one left off. I demoed this concretely: pause a run, kill `uvicorn`, restart it, resume from the run-history UI, and it completes normally.

**Q: Why didn't you stream tokens directly from the reasoning node?**
> The reasoning node's output is one field inside a strict-JSON decision object (`{"thought": ..., "final_answer": ...}`) — there's no way to stream a partial JSON field meaningfully, and the JSON-output constraint actively works against natural prose formatting. Instead I added a dedicated `finalize` node that re-renders the critic-*accepted* draft with a second call, this time using the real streaming API with a presentation-only prompt. It costs one extra LLM call per run, but buys real token streaming, guaranteed markdown, and a clean single place to compute the final answer's sources.

**Q: Why treat a tool with no MCP annotation as a write action?**
> Fail-safe design. The alternative — defaulting an unannotated tool to read-only — means a tool added later without remembering to annotate it silently bypasses human approval on real state changes. Defaulting to "gated" means the failure mode of forgetting an annotation is "an extra approval click," not "an unreviewed write."

**Q: Tell me about a bug you found through your own testing, not a user report.**
> While adding the durable checkpointer, I load-tested a write-action run and hit `sqlite3.OperationalError: database is locked` — a slow tool call (a hung MCP subprocess, in the old design) was holding the checkpoint's write transaction open for over a minute, and my separate run-history writer, sharing the same SQLite file, couldn't get in. Fixing it properly took two changes: moving the history writer off the FastAPI event loop entirely (`asyncio.to_thread`, so contention costs a worker thread, not the whole server's responsiveness) and rewriting the MCP client to hold one persistent session instead of spawning a subprocess per call, which removed the actual hang. I verified the fix by polling `/health` concurrently during a stuck run and confirming zero latency spikes.

---

## 11. Reference: current free models (re-verify before relying on these)
| Role | Model | Notes (June–July 2026) |
|------|-------|-------------------|
| Agent reasoning + finalize streaming | Groq `openai/gpt-oss-120b` | Fast, strong free reasoning; ~30 RPM / 1K RPD free. |
| Fallback / judge | Gemini `gemini-3.5-flash` | Free tier is Flash-only (Pro is paid since Apr 2026). |
| Web search | DuckDuckGo via `ddgs` | No key. Tavily / Gemini grounding are alternatives. |

> Groq deprecated the Llama models in June 2026 — confirm live IDs in the [Groq console](https://console.groq.com/docs/models).

New library dependency added in this round of work: `langgraph-checkpoint-sqlite` (brings `aiosqlite`) for the durable `AsyncSqliteSaver` — verify its version against the installed `langgraph-checkpoint` before upgrading either independently, they're version-coupled.

---

## Sources / further reading
- **LangGraph / HITL:** [LangGraph (LangChain)](https://www.langchain.com/langgraph) · [Workflows & agents docs](https://docs.langchain.com/oss/python/langgraph/workflows-agents) · [HITL that works in production](https://medium.com/aimonks/building-agents-with-langgraph-human-in-the-loop-interactions-that-actually-work-in-production-d7d038625260) · [LangGraph state management](https://eastondev.com/blog/en/posts/ai/20260424-langgraph-agent-architecture/)
- **LangGraph persistence:** [Persistence / checkpointers docs](https://docs.langchain.com/oss/python/langgraph/persistence) · [`langgraph-checkpoint-sqlite` (PyPI)](https://pypi.org/project/langgraph-checkpoint-sqlite/)
- **MCP 2026:** [MCP 2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/) · [MCP roadmap](https://modelcontextprotocol.io/development/roadmap) · [Official MCP Registry](https://registry.modelcontextprotocol.io/) · [Everything about MCP in 2026 (WorkOS)](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026) · [MCP tool annotations spec](https://modelcontextprotocol.io/docs/concepts/tools#tool-annotations)
- **Agent evaluation:** [Agent eval frameworks 2026](https://futureagi.com/blog/agent-evaluation-frameworks-2026) · [AI agent evaluation: metrics & failures](https://www.morphllm.com/ai-agent-evaluation) · [Top agent eval frameworks](https://blog.agentailor.com/posts/top-ai-agent-eval-frameworks-2026)
- **Portfolio:** [AI agent portfolio projects that get you hired 2026](https://agenticcareers.co/blog/ai-agent-portfolio-projects-get-hired-2026) · [Top AI agent projects (DataCamp)](https://www.datacamp.com/blog/top-ai-agent-projects)






backend
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --port 8001
frontend
npm.cmd run dev
backend to remve dataset
Remove-Item .\tasks.db -ErrorAction SilentlyContinue
