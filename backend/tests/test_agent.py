"""TaskPilot tests — no API key required (the LLM is stubbed with scripted JSON).

Covers the three highest-risk seams:
  * parse_json     — the LLM-action parser must survive fences/prose/garbage
  * MCP round-trip — server↔client over stdio, real SQLite write
  * full agent run — plan → approval interrupt → resume → MCP write → critic → final
"""
from __future__ import annotations

import os
import tempfile

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent import graph as G
from app.agent.graph import build_graph, parse_json

# Tests use a fresh in-memory MemorySaver graph (fast, isolated) rather than
# the API's durable get_graph(), which opens a real AsyncSqliteSaver file.


# ---- parse_json ----------------------------------------------------------
def test_parse_json_plain():
    assert parse_json('{"action": {"tool": "web_search"}}') == {
        "action": {"tool": "web_search"}
    }


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_prose():
    assert parse_json('Sure! {"final_answer": "hi"} done') == {"final_answer": "hi"}


def test_parse_json_garbage_returns_empty():
    assert parse_json("not json at all") == {}
    assert parse_json("") == {}


# ---- MCP server <-> client round-trip ------------------------------------
@pytest.mark.asyncio
async def test_mcp_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPILOT_DB", str(tmp_path / "t.db"))
    # Rebuild settings so db_path picks up the temp DB.
    from app.config import get_settings
    get_settings.cache_clear()

    from app.tools.mcp_client import list_mcp_tools, call_mcp_tool, shutdown_mcp_session

    # The MCP client holds one persistent session across the whole test
    # process — reset it so the next call spawns a fresh subprocess that
    # actually sees the temp TASKPILOT_DB set above.
    await shutdown_mcp_session()

    tools_raw = await list_mcp_tools()
    tools = {t["name"] for t in tools_raw}
    assert {"add_task", "list_tasks", "complete_task"} <= tools
    # Annotation-derived write flags (see mcp_server/server.py + mcp_client.py).
    by_name = {t["name"]: t for t in tools_raw}
    assert by_name["add_task"]["write"] is True
    assert by_name["list_tasks"]["write"] is False
    assert by_name["complete_task"]["write"] is True

    created = await call_mcp_tool("add_task", {"title": "Write tests"})
    assert "Write tests" in created
    listed = await call_mcp_tool("list_tasks", {})
    assert "Write tests" in listed


# ---- full agent flow with a scripted LLM ---------------------------------
@pytest.mark.asyncio
async def test_full_agent_flow_with_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPILOT_DB", str(tmp_path / "t.db"))
    from app.config import get_settings
    get_settings.cache_clear()
    # Force a clean registry + MCP session so both pick up the temp DB env.
    G._registry = None
    G._WRITE_TOOLS.clear()
    from app.tools.mcp_client import shutdown_mcp_session
    await shutdown_mcp_session()

    async def fake_llm(messages, **kw):
        sysmsg, user = messages[0]["content"], messages[-1]["content"]
        if "numbered plan" in sysmsg:
            return "1. Create the task. 2. Confirm."
        if "strict critic" in sysmsg.lower():
            return '{"verdict":"accept","reason":"done"}'
        if '"status": "open"' in user or "REJECTED" in user:
            return '{"thought":"created","final_answer":"Task created."}'
        return '{"thought":"create it","action":{"tool":"add_task","args":{"title":"Buy milk"}}}'

    monkeypatch.setattr(G, "_llm", fake_llm)
    # finalize_node streams via chat_stream directly (not _llm) — stub it too,
    # or this test would make a real network call.
    monkeypatch.setattr(G, "chat_stream", lambda messages, **kw: iter(["Task created."]))

    from langgraph.types import Command

    graph = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "test1"}, "recursion_limit": 60}
    inp = {"task": "add a task to buy milk", "max_steps": 5, "max_critiques": 1}

    events: list[str] = []
    interrupted = False
    async for mode, chunk in graph.astream(inp, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom":
            events.append(chunk["type"])
        elif mode == "updates" and "__interrupt__" in chunk:
            interrupted = True
            break

    assert interrupted, "agent should pause for approval before a write action"
    assert "plan" in events and "thought" in events

    # Resume with approval → the write should execute and the run should finish.
    after: list[str] = []
    async for mode, chunk in graph.astream(
        Command(resume={"approved": True}), cfg, stream_mode=["custom", "updates"]
    ):
        if mode == "custom":
            after.append(chunk["type"])
    assert "action" in after and "final" in after

    # The MCP write really happened.
    from mcp_server import db
    titles = [t["title"] for t in db.list_tasks()]
    assert "Buy milk" in titles


@pytest.mark.asyncio
async def test_premature_finish_is_forced_to_use_a_tool(monkeypatch):
    """If the model tries to finish without calling any tool, the guard forces one."""
    G._registry = None
    G._WRITE_TOOLS.clear()

    calls = {"n": 0}

    async def fake_llm(messages, **kw):
        sysmsg = messages[0]["content"]
        if "numbered plan" in sysmsg:
            return "1. Search. 2. Answer."
        calls["n"] += 1
        # First reason call: try to bail out without using any tool.
        if calls["n"] == 1:
            return '{"thought":"I need a URL","final_answer":"I cannot proceed."}'
        # After the guard nudge: comply and call a tool.
        return '{"thought":"searching","action":{"tool":"web_search","args":{"query":"x"}}}'

    monkeypatch.setattr(G, "_llm", fake_llm)
    # This run reaches finalize_node (forced finish → critic-skip → finalize),
    # which streams via chat_stream directly — stub it too.
    monkeypatch.setattr(G, "chat_stream", lambda messages, **kw: iter(["done."]))

    graph = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "guard1"}, "recursion_limit": 20}
    inp = {"task": "research something", "max_steps": 1, "max_critiques": 0}

    actions = []
    async for mode, chunk in graph.astream(inp, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom" and chunk["type"] == "action":
            actions.append(chunk["data"]["tool"])
    # The guard turned a premature finish into a real web_search call.
    assert "web_search" in actions


@pytest.mark.asyncio
async def test_critic_mandate_ignored_forces_compliance(tmp_path, monkeypatch):
    """If the model ignores the critic's revise mandate — trying to finish
    again instead of calling the tool the critic demanded, sometimes even
    hallucinating a call it never made — the guard forces one more corrective
    attempt at the mandated tool before giving up. Regression test for a bug
    caught live: an agent run saw the critic say "call add_task", the model
    replied as if it had already called read_url (it hadn't), and the run
    ended in a generic "I couldn't gather enough information" apology."""
    monkeypatch.setenv("TASKPILOT_DB", str(tmp_path / "t.db"))
    from app.config import get_settings
    get_settings.cache_clear()
    G._registry = None
    G._WRITE_TOOLS.clear()
    from app.tools.mcp_client import shutdown_mcp_session
    await shutdown_mcp_session()

    calls = {"n": 0}

    async def fake_llm(messages, **kw):
        sysmsg = messages[0]["content"]
        if "numbered plan" in sysmsg:
            return "1. Check existing tasks. 2. Add the task."
        if "strict critic" in sysmsg.lower():
            return ('{"verdict":"revise","reason":"no write tool called",'
                     '"feedback":"You must call add_task now."}')
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"thought":"checking","action":{"tool":"list_tasks","args":{}}}'
        if calls["n"] == 2:
            # Tries to finish without ever calling add_task -> critic revises.
            return '{"thought":"done","final_answer":"I added the task."}'
        if calls["n"] == 3:
            # MANDATE IGNORED: tries to finish again instead of complying.
            return '{"thought":"still working","final_answer":"Working on it."}'
        # After the guard's corrective nudge: finally comply.
        return '{"thought":"complying","action":{"tool":"add_task","args":{"title":"Buy milk"}}}'

    monkeypatch.setattr(G, "_llm", fake_llm)

    graph = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "mandate1"}, "recursion_limit": 60}
    inp = {"task": "add a task to buy milk", "max_steps": 8, "max_critiques": 1}

    interrupt_payload = None
    async for mode, chunk in graph.astream(inp, cfg, stream_mode=["custom", "updates"]):
        if mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value

    assert interrupt_payload is not None, "the forced add_task call should trigger the approval gate"
    assert interrupt_payload["tool"] == "add_task"


@pytest.mark.asyncio
async def test_unresolved_placeholder_in_args_triggers_corrective_retry(monkeypatch):
    """If the model writes a literal, unfilled template placeholder like
    '[Winner Name]' into a tool call's arguments — copied verbatim from its
    own plan instead of resolved to a real value — one corrective nudge asks
    it to use the real value before the action ever reaches the approval
    gate. Regression test for a bug caught live: an agent created a task with
    notes literally reading "Winner: [Winner Name]"."""
    G._registry = None
    G._WRITE_TOOLS.clear()

    calls = {"n": 0}

    async def fake_llm(messages, **kw):
        sysmsg = messages[0]["content"]
        if "numbered plan" in sysmsg:
            return "1. Search. 2. Add the task."
        calls["n"] += 1
        if calls["n"] == 1:
            # Writes a literal, unresolved placeholder instead of a real value.
            return ('{"thought":"adding","action":{"tool":"add_task",'
                     '"args":{"title":"Watch highlights","notes":"Winner: [Winner Name]"}}}')
        # After the corrective nudge: uses the real value.
        return ('{"thought":"fixed","action":{"tool":"add_task",'
                 '"args":{"title":"Watch highlights","notes":"Winner: Max Verstappen"}}}')

    monkeypatch.setattr(G, "_llm", fake_llm)

    graph = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "placeholder1"}, "recursion_limit": 20}
    inp = {"task": "add a task to watch highlights", "max_steps": 8, "max_critiques": 1}

    interrupt_payload = None
    async for mode, chunk in graph.astream(inp, cfg, stream_mode=["custom", "updates"]):
        if mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value

    assert interrupt_payload is not None
    assert "[Winner Name]" not in interrupt_payload["args"]["notes"]
    assert interrupt_payload["args"]["notes"] == "Winner: Max Verstappen"


# ---- _collect_sources ------------------------------------------------------
def test_collect_sources_derives_pages_and_searches():
    """Sources are derived from the scratchpad, not model-emitted citations —
    verify the title-scraping and error-exclusion logic directly."""
    search_obs = (
        "1. James Webb Telescope News\n   https://example.com/jwst\n   A snippet...\n"
        "2. Another Result\n   https://example.com/other\n   Another snippet"
    )
    state = {
        "scratchpad": [
            {"tool": "web_search", "args": {"query": "JWST latest"}, "observation": search_obs},
            {"tool": "read_url", "args": {"url": "https://example.com/jwst"}, "observation": "Full page text."},
            {"tool": "read_url", "args": {"url": "https://bad.example/404"}, "observation": "ERROR: HTTP 404"},
        ]
    }
    sources = G._collect_sources(state)
    assert sources == [
        {"kind": "search", "query": "JWST latest"},
        {"kind": "page", "url": "https://example.com/jwst", "title": "James Webb Telescope News"},
    ]


def test_collect_sources_empty_scratchpad():
    assert G._collect_sources({"scratchpad": []}) == []


# ---- runs_store round-trip --------------------------------------------------
def test_runs_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB", str(tmp_path / "agent.db"))
    from app.config import get_settings
    get_settings.cache_clear()

    from app import runs_store as R

    run_id = "test-run-1"
    R.create_run(run_id, "test task")
    R.append_event(run_id, "plan", {"text": "1. Do the thing."})
    R.append_event(run_id, "action", {"tool": "web_search", "args": {"query": "x"}})
    R.set_status(run_id, "paused")

    summary = R.get_run(run_id)
    assert summary["status"] == "paused"
    assert summary["task"] == "test task"

    events = R.get_run_events(run_id)
    assert [e["event"] for e in events] == ["plan", "action"]
    assert events[1]["data"]["tool"] == "web_search"

    R.finish_run(run_id, "done", 4.2, final_preview="All done.")
    assert R.get_run(run_id)["status"] == "done"
    assert R.get_run(run_id)["elapsed_s"] == 4.2

    runs = R.list_runs()
    assert any(r["id"] == run_id for r in runs)

    R.delete_run(run_id)
    assert R.get_run(run_id) is None
    get_settings.cache_clear()
