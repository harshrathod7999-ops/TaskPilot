"""MCP client — a persistent stdio session to our custom MCP server.

Rewritten from a fresh-subprocess-per-call design. That was simple but had
two real costs: a subprocess spawn (~1-2s on Windows) on *every* tool call
and every /health check, and — observed live while building this project —
an occasional 60+ second hang that held the LangGraph checkpoint write lock
open for the same duration and produced flaky "database is locked" errors
elsewhere in the app (see GUIDE.md's hard-parts section). A persistent
session removes both: one subprocess for the life of the API process, reused
by every call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..config import get_settings

logger = logging.getLogger("taskpilot.mcp")


def _server_params() -> StdioServerParameters:
    s = get_settings()
    env = {**os.environ, "TASKPILOT_DB": str(s.db_path), "PYTHONIOENCODING": "utf-8"}
    return StdioServerParameters(
        command=sys.executable,
        args=[str(s.mcp_server_script)],
        env=env,
        cwd=str(s.backend_root),
    )


def _content_to_text(result) -> str:
    """Flatten an MCP tool result into a string the LLM can read."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured)
    return "(tool returned no content)"


def _is_write_tool(tool) -> bool:
    """A tool is treated as a WRITE (approval-gated) action unless it's
    explicitly marked read-only via MCP tool annotations. Fail-safe by
    design: a tool with no annotations at all — e.g. a new one added without
    remembering to annotate it — defaults to gated rather than silently
    bypassing human approval."""
    ann = getattr(tool, "annotations", None)
    return not (ann and getattr(ann, "readOnlyHint", False))


class _MCPSession:
    """Owns one long-lived MCP stdio connection for the process lifetime.

    ``stdio_client``/``ClientSession`` are anyio-based context managers whose
    cancel scopes are tied to the task that entered them — entering them from
    whichever request task happens to need a tool first would break the
    moment a *different* task tried to use the session later. A dedicated
    supervisor task owns the connection end-to-end; every other task only
    ever calls ``session.call_tool``/``list_tools``, which ``ClientSession``
    safely multiplexes over the one connection.
    """

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._ready: asyncio.Event | None = None
        self._shutdown: asyncio.Event | None = None
        self._supervisor: asyncio.Task | None = None
        self._start_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _rebind_if_new_loop(self) -> None:
        """Recreate the loop-bound primitives when called from a different
        event loop than last time. In production there is exactly one loop
        for the app's whole life, so this never actually fires; it exists so
        the test suite — where each test can run on its own fresh event loop —
        doesn't crash reusing this module-level singleton across tests. Any
        supervisor task from a prior loop belongs to a loop that's already
        gone, so it's just dropped rather than awaited/cancelled."""
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self._session = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._start_lock = asyncio.Lock()
        self._supervisor = None

    async def _run(self) -> None:
        try:
            async with stdio_client(_server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._shutdown.wait()
        except Exception:
            logger.exception("[mcp] session supervisor crashed")
        finally:
            self._session = None
            self._ready.set()  # unblock any waiter — they'll see _session is None

    async def ensure_started(self) -> None:
        self._rebind_if_new_loop()
        async with self._start_lock:
            if self._supervisor is None or self._supervisor.done():
                self._ready.clear()
                self._shutdown.clear()
                self._supervisor = asyncio.create_task(self._run())
        await self._ready.wait()

    async def restart(self) -> None:
        """Tear down a dead/stuck session and start a fresh one."""
        self._rebind_if_new_loop()
        async with self._start_lock:
            supervisor = self._supervisor
        if supervisor and not supervisor.done():
            self._shutdown.set()
            try:
                await asyncio.wait_for(supervisor, timeout=5)
            except Exception:
                supervisor.cancel()
        async with self._start_lock:
            self._session = None
            self._supervisor = None
        await self.ensure_started()

    async def shutdown(self) -> None:
        self._rebind_if_new_loop()
        async with self._start_lock:
            supervisor = self._supervisor
        if supervisor and not supervisor.done():
            self._shutdown.set()
            try:
                await asyncio.wait_for(supervisor, timeout=5)
            except Exception:
                supervisor.cancel()

    async def get(self) -> ClientSession:
        await self.ensure_started()
        if self._session is None:
            raise RuntimeError("MCP session failed to start")
        return self._session


_session = _MCPSession()


async def _with_session(fn):
    """Run fn(session) against the persistent session. On a connection-shaped
    failure, restart the session once and retry before giving up — this is
    what makes a crashed/stuck MCP subprocess self-heal instead of wedging
    every future tool call."""
    try:
        return await fn(await _session.get())
    except Exception as e:
        logger.warning("[mcp] call failed (%s) — restarting session and retrying once", e)
        await _session.restart()
        return await fn(await _session.get())


async def list_mcp_tools() -> list[dict]:
    """Discover the MCP server's tools (name, description, JSON schema, write flag)."""

    async def go(session: ClientSession):
        resp = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": (t.description or "").strip(),
                "schema": t.inputSchema,
                "write": _is_write_tool(t),
            }
            for t in resp.tools
        ]

    return await _with_session(go)


async def call_mcp_tool(name: str, args: dict) -> str:
    """Call one MCP tool and return its result as text."""

    async def go(session: ClientSession):
        result = await session.call_tool(name, args or {})
        if getattr(result, "isError", False):
            return f"ERROR from tool {name}: {_content_to_text(result)}"
        return _content_to_text(result)

    return await _with_session(go)


async def shutdown_mcp_session() -> None:
    """Close the persistent session so the next call starts a fresh one.

    Called once from the FastAPI lifespan on shutdown; also reused by tests
    that change TASKPILOT_DB mid-suite and need the MCP server subprocess to
    pick up the new path.
    """
    await _session.shutdown()
