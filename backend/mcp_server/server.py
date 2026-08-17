"""TaskPilot's custom MCP server — built from scratch with the MCP Python SDK.

This is the project's key differentiator: instead of hard-coding tools into the
agent, we expose a real **Model Context Protocol** server that any MCP client
(our agent, Claude Desktop, Cursor, etc.) can connect to and call. It exposes a
small task-tracker as the agent's "action" surface:

  * add_task       — create a task            (a WRITE action → human-approval gated)
  * list_tasks     — read current tasks       (safe, no approval)
  * complete_task  — mark a task done         (a WRITE action → human-approval gated)

Run standalone (stdio transport, how the agent launches it):
    python mcp_server/server.py
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from db import add_task as _add_task
from db import complete_task as _complete_task
from db import init_db
from db import list_tasks as _list_tasks

mcp = FastMCP("taskpilot-tools")

# Annotations are how the client (app/tools/mcp_client.py) decides whether a
# tool needs the human-approval gate: readOnlyHint=True is the only way to
# mark a tool safe to auto-run. A tool with no annotations at all defaults to
# gated (fail-safe) — see `_is_write_tool` in mcp_client.py.


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def add_task(title: str, notes: str = "", due: str = "") -> dict:
    """Create a new task in the tracker.

    Args:
        title: Short task title (required).
        notes: Optional longer description / context.
        due: Optional due date as free text (e.g. '2026-07-01' or 'next Friday').

    Returns the created task record.
    """
    return _add_task(title, notes, due)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_tasks(status: str = "") -> list[dict]:
    """List tasks, optionally filtered by status ('open' or 'done')."""
    return _list_tasks(status)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def complete_task(task_id: int) -> dict:
    """Mark the task with the given id as done."""
    return _complete_task(task_id)


if __name__ == "__main__":
    init_db()
    mcp.run()  # stdio transport
