"""Langfuse tracing setup, shared across all 5 projects.

Tracing is *optional*: if Langfuse keys are absent the helpers degrade to
no-ops so the projects still run locally without an account. When keys are
present, every LLM / tool / retrieval call can be wrapped in a span.

Usage
-----
    from shared.tracing import get_client, observe

    @observe(name="retrieve")
    def retrieve(query: str): ...

LiteLLM is wired to Langfuse separately in ``shared/llm.py`` via
``litellm.callbacks`` so model calls are traced automatically.
"""
from __future__ import annotations

import functools
import os
from typing import Any, Callable, Optional

_LANGFUSE_AVAILABLE = False
try:  # langfuse is optional at runtime
    from langfuse import Langfuse, observe as _lf_observe  # type: ignore

    _LANGFUSE_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    Langfuse = None  # type: ignore
    _lf_observe = None  # type: ignore


_client: Optional["Langfuse"] = None


def tracing_enabled() -> bool:
    """True only when langfuse is installed AND keys are configured."""
    return (
        _LANGFUSE_AVAILABLE
        and bool(os.getenv("LANGFUSE_PUBLIC_KEY"))
        and bool(os.getenv("LANGFUSE_SECRET_KEY"))
    )


def get_client() -> Optional["Langfuse"]:
    """Return a singleton Langfuse client, or None if tracing is disabled."""
    global _client
    if not tracing_enabled():
        return None
    if _client is None:
        _client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    return _client


def observe(*, name: Optional[str] = None) -> Callable:
    """Decorator that wraps a function in a Langfuse span when enabled.

    Falls back to a transparent pass-through decorator when tracing is off,
    so callers never need to branch on whether Langfuse is configured.
    """

    def decorator(func: Callable) -> Callable:
        if tracing_enabled() and _lf_observe is not None:
            return _lf_observe(name=name or func.__name__)(func)

        @functools.wraps(func)
        def passthrough(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return passthrough

    return decorator


def flush() -> None:
    """Flush pending traces (call before process exit / after a request)."""
    client = get_client()
    if client is not None:
        try:
            client.flush()
        except Exception:  # pragma: no cover - best effort
            pass
