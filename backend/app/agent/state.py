"""Agent state shared across LangGraph nodes."""
from __future__ import annotations

from typing import Any, TypedDict


class Step(TypedDict):
    thought: str
    tool: str
    args: dict[str, Any]
    observation: str


class AgentState(TypedDict, total=False):
    task: str
    plan: str
    scratchpad: list[Step]      # the running plan→act→observe history
    step: int                   # tool executions so far
    max_steps: int
    critiques: int              # times the critic sent work back
    max_critiques: int
    pending: dict[str, Any] | None   # {tool, args} chosen by reason, awaiting act/approval
    final_answer: str | None
    forced_finish: bool         # set when the step limit is hit
    status: str
