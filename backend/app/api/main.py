"""TaskPilot FastAPI app: run the agent (SSE trace), approve write actions, list tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import logging.config
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from .. import runs_store
from ..agent.graph import close_graph, get_graph, get_registry
from ..config import get_settings
from ..models import (
    ApprovalRequest,
    RunDetail,
    RunRequest,
    RunSummary,
    TaskItem,
)
from ..tools.mcp_client import shutdown_mcp_session
from ..tracing_compat import flush

settings = get_settings()

# ---- logging setup ----------------------------------------------------------
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        }
    },
    "loggers": {
        "taskpilot": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "uvicorn.access": {"level": "WARNING"},   # suppress per-request noise
    },
    "root": {"level": "WARNING"},
})
logger = logging.getLogger("taskpilot.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the tool registry once at startup (also spawns the MCP server to
    # discover its tools), so the first run isn't slowed by discovery.
    try:
        await get_registry()
    except Exception:
        pass
    # Open the durable checkpoint DB up front too, so a paused run created
    # before a restart is resumable as soon as the server is back up.
    try:
        await get_graph()
    except Exception:
        logger.exception("[api] failed to open the agent checkpoint DB")
    yield
    await close_graph()
    await shutdown_mcp_session()


app = FastAPI(title="TaskPilot API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# Transient frames that aren't worth replaying later: `status` is progress
# noise, and `final_token` is superseded by the authoritative `final` frame.
_UNPERSISTED_EVENTS = {"status", "final_token"}


async def _record(run_id: str, event: str, data: dict) -> None:
    """Persist one trace event so the run survives a restart and can be replayed.

    Runs in a worker thread: ``agent.db`` is shared with the LangGraph
    AsyncSqliteSaver, whose write transaction can stay open for as long as
    the tool call it's checkpointing around (a slow web search or a hung MCP
    subprocess can take a minute-plus). ``runs_store``'s retry-on-lock logic
    blocks synchronously while that clears — doing that on the event loop
    would stall every other request for the same duration. If persistence
    still fails after retries, the live run is unaffected; only that one
    entry is missing from replay history.
    """
    try:
        await asyncio.to_thread(runs_store.append_event, run_id, event, data)
    except Exception:
        logger.exception("[api] failed to persist event=%s run=%s", event, run_id[:8])


async def _stream_graph(graph_input, thread_id: str):
    """Stream agent events; emit `paused` on an approval interrupt, else `done`.

    Most events are recorded to the runs store as they're emitted, and a
    client disconnect (Stop button) or hard error is caught so the run's
    final status is always set — nothing is left dangling as "running".
    """
    graph = await get_graph()
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
    t0 = time.monotonic()
    final_text = ""
    try:
        async for mode, chunk in graph.astream(
            graph_input, config, stream_mode=["custom", "updates"]
        ):
            if mode == "custom":
                etype, edata = chunk.get("type", "status"), chunk.get("data", {})
                if etype not in _UNPERSISTED_EVENTS:
                    await _record(thread_id, etype, edata)
                if etype == "final":
                    final_text = edata.get("text", "")
                yield _sse(etype, edata)
            elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                # The agent hit a write action and paused for human approval.
                payload = {}
                try:
                    payload = chunk["__interrupt__"][0].value or {}
                except Exception:
                    pass
                logger.info("[api] thread=%s paused for approval tool=%s", thread_id[:8], payload.get("tool"))
                await _record(thread_id, "approval_required", payload)
                yield _sse("approval_required", payload)
                await asyncio.to_thread(runs_store.set_status, thread_id, "paused")
                yield _sse("paused", {"thread_id": thread_id})
                return
    except asyncio.CancelledError:
        # Client disconnected (Stop button, tab close). The checkpointer already
        # persisted state up to the last completed node — nothing more to save.
        logger.info("[api] thread=%s stopped by client disconnect", thread_id[:8])
        await asyncio.to_thread(runs_store.set_status, thread_id, "stopped")
        raise
    except Exception as e:  # never leave the UI hanging
        msg = str(e)
        low = msg.lower()
        logger.error("[api] thread=%s run error after %.1fs: %s", thread_id[:8], time.monotonic() - t0, msg[:200])
        if any(k in low for k in ("rate limit", "ratelimit", "429", "503",
                                  "unavailable", "high demand", "overloaded")):
            friendly = ("All three free model providers (Groq, Gemini Flash, Gemini Flash Lite) "
                        "are momentarily rate-limited. This is transient — wait ~1 minute and retry. "
                        "If this happens often, space out your requests or add API keys with higher quotas.")
        else:
            friendly = f"The run failed: {msg[:240]}"
        await _record(thread_id, "error", {"message": friendly})
        await asyncio.to_thread(runs_store.finish_run, thread_id, "error", round(time.monotonic() - t0, 1))
        yield _sse("error", {"message": friendly})
        return
    elapsed = time.monotonic() - t0
    logger.info("[api] thread=%s done in %.1fs", thread_id[:8], elapsed)
    await asyncio.to_thread(
        runs_store.finish_run, thread_id, "done", round(elapsed, 1), final_preview=final_text[:160]
    )
    yield _sse("done", {"thread_id": thread_id, "elapsed_s": round(elapsed, 1)})
    flush()


@app.post("/run")
async def run(req: RunRequest) -> StreamingResponse:
    thread_id = uuid.uuid4().hex
    logger.info("[api] /run thread=%s task=%r", thread_id[:8], req.task[:80])
    await asyncio.to_thread(runs_store.create_run, thread_id, req.task)
    graph_input = {
        "task": req.task,
        "max_steps": req.max_steps or settings.agent_max_steps,
        "max_critiques": settings.agent_max_critiques,
    }

    async def gen():
        yield _sse("status", {"phase": "started", "thread_id": thread_id})
        async for frame in _stream_graph(graph_input, thread_id):
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/resume")
async def resume(req: ApprovalRequest) -> StreamingResponse:
    """Resume a paused run after the human approves/rejects a write action.

    Works even after a backend restart: the AsyncSqliteSaver checkpoint for
    this thread_id was persisted to disk, so LangGraph can pick the run back
    up from exactly where it paused.
    """
    await asyncio.to_thread(runs_store.set_status, req.thread_id, "running")
    cmd = Command(resume={"approved": req.approved, "edited_args": req.edited_args})

    async def gen():
        yield _sse("status", {"phase": "resumed", "thread_id": req.thread_id})
        async for frame in _stream_graph(cmd, req.thread_id):
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/runs", response_model=list[RunSummary])
def list_runs(limit: int = 50) -> list[RunSummary]:
    return [RunSummary(**r) for r in runs_store.list_runs(limit)]


@app.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str) -> RunDetail:
    summary = runs_store.get_run(run_id)
    if summary is None:
        raise HTTPException(404, "Unknown run.")
    return RunDetail(**summary, events=runs_store.get_run_events(run_id))


@app.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    runs_store.delete_run(run_id)
    return {"deleted": run_id}


@app.get("/tasks", response_model=list[TaskItem])
def tasks() -> list[TaskItem]:
    from mcp_server import db
    return [TaskItem(**t) for t in db.list_tasks()]


@app.get("/health")
async def health() -> dict:
    tools_ok, tool_names, err = True, [], None
    try:
        reg = await get_registry()
        tool_names = list(reg.keys())
    except Exception as e:
        tools_ok, err = False, str(e)[:200]
    return {"status": "ok", "tools_ok": tools_ok, "tools": tool_names, "error": err}


@app.get("/")
def root() -> dict:
    return {"name": "TaskPilot", "docs": "/docs", "health": "/health"}
