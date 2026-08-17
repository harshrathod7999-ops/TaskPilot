"""Single LLM entrypoint for the whole portfolio — FREE models only.

Design goals (this file is itself a portfolio detail):
  * One ``chat()`` function every project imports.
  * **3-model chain:** Groq primary → Gemini Flash → Gemini Flash Lite.
    Each has an independent rate-limit quota; exhausting one falls to the next.
  * Exponential backoff with jitter on 429s before falling back.
  * Inter-provider cooldown sleep so rate limits have time to reset.
  * Langfuse tracing wired into LiteLLM callbacks (auto, optional).
  * Providers swappable in ONE place via env vars.

All model IDs are free / free-tier (verified 2026-06-22). Re-verify in the
Groq console / Google AI Studio if running much later — Groq deprecates
models and Gemini renames Flash tiers frequently.

Usage
-----
    from shared.llm import chat
    text = chat([{"role": "user", "content": "Hello"}])

    # JSON / structured output
    data = chat(messages, response_format={"type": "json_object"})

    # Streaming
    for token in chat_stream(messages):
        print(token, end="")
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Iterator, Optional

import litellm
from litellm import completion
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from .tracing import observe

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
# 3-model chain: Groq (fast reasoning) → Gemini Flash → Gemini Flash Lite.
# Each has its own independent rate-limit bucket on the free tier:
#   Groq:           ~30 RPM / 1 K RPD
#   Gemini Flash:   ~15 RPM / 1 M TPD
#   Gemini Lite:    ~30 RPM / 1 M TPD  ← separate quota from Flash
PRIMARY_MODEL    = os.getenv("PRIMARY_MODEL",    "groq/openai/gpt-oss-120b")
FALLBACK_MODEL   = os.getenv("FALLBACK_MODEL",   "gemini/gemini-3.5-flash")
FALLBACK_MODEL_2 = os.getenv("FALLBACK_MODEL_2", "gemini/gemini-3.1-flash-lite")

# Errors that justify a retry-then-fallback (transient / capacity).
_RETRYABLE = (
    RateLimitError,
    ServiceUnavailableError,
    InternalServerError,
    APIConnectionError,
    Timeout,
)

# Keep LiteLLM quiet and resilient.
litellm.drop_params = True          # silently drop params a provider doesn't support
litellm.suppress_debug_info = True
# Quiet LiteLLM's WARNING-level log noise (remote cost-map fetch failures and the
# Gemini 3+ `temperature` deprecation notice — the param still functions). Real
# errors still surface at ERROR level.
logging.getLogger("LiteLLM").setLevel(logging.ERROR)

_log = logging.getLogger("llm.router")

# NOTE: we deliberately do NOT use LiteLLM's built-in `success_callback =
# ["langfuse"]` string integration — it targets the pre-v3 Langfuse SDK
# (`langfuse.version.__version__`) and raises AttributeError against the
# OTel-based Langfuse v3+ SDK pinned here, silently killing every call.
# `chat`/`chat_stream` below are wrapped in our own `@observe` span instead,
# which uses the current SDK directly and degrades to a no-op when tracing
# is disabled.


# --------------------------------------------------------------------------
# Core helpers
# --------------------------------------------------------------------------
def _backoff_sleep(attempt: int, base: float = 1.0, cap: float = 60.0) -> None:
    """Exponential backoff with full jitter (cap raised to 60 s for free tiers)."""
    delay = min(cap, base * (2 ** attempt))
    time.sleep(random.uniform(0, delay))


def _call(model: str, messages: list[dict], stream: bool = False, **kwargs: Any):
    """One LiteLLM completion call (no retry logic here)."""
    return completion(model=model, messages=messages, stream=stream, **kwargs)


def _attempt_model(
    model: str,
    messages: list[dict],
    *,
    max_retries: int,
    stream: bool,
    **kwargs: Any,
):
    """Try a single model with backoff on transient errors.

    Returns the response on success, or raises the last error so the caller
    can decide whether to fall back to another provider.
    """
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return _call(model, messages, stream=stream, **kwargs)
        except _RETRYABLE as err:
            last_err = err
            if attempt < max_retries:
                _backoff_sleep(attempt)
            continue
    assert last_err is not None
    raise last_err


def _model_chain(
    primary: str,
    fallback_model: Optional[str],
) -> list[str]:
    """Return the ordered list of models to try, deduped."""
    seen: set[str] = set()
    chain: list[str] = []
    for m in [primary, fallback_model, FALLBACK_MODEL_2]:
        if m and m not in seen:
            seen.add(m)
            chain.append(m)
    return chain


@observe(name="llm-chat")
def chat(
    messages: list[dict],
    *,
    model: str = PRIMARY_MODEL,
    fallback_model: Optional[str] = FALLBACK_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    max_retries: int = 4,
    metadata: Optional[dict] = None,
    **kwargs: Any,
) -> str:
    """Get a completion as a string. Tries a 3-model chain on rate-limit errors.

    Chain: PRIMARY_MODEL → FALLBACK_MODEL → FALLBACK_MODEL_2 (Flash Lite).
    Each model retries with exponential backoff before moving to the next.
    A short inter-provider sleep gives rate limits time to recover.

    Parameters
    ----------
    messages : OpenAI-style chat messages.
    model : primary model id (LiteLLM format, e.g. ``groq/openai/gpt-oss-120b``).
    fallback_model : first fallback; set to ``None`` to disable the chain.
    metadata : forwarded to Langfuse (e.g. ``{"trace_name": "documind-answer"}``).
    """
    if metadata:
        kwargs.setdefault("metadata", metadata)

    chain = _model_chain(model, fallback_model)
    last_err: Optional[Exception] = None
    for i, m in enumerate(chain):
        if i > 0:
            # Brief cooldown between providers so rate limits have time to reset.
            cooldown = random.uniform(2, 6)
            _log.warning("provider %s exhausted — waiting %.1fs then trying %s", chain[i - 1], cooldown, m)
            time.sleep(cooldown)
        try:
            resp = _attempt_model(
                m, messages, max_retries=max_retries, stream=False,
                temperature=temperature, max_tokens=max_tokens, **kwargs,
            )
            if i > 0:
                _log.info("succeeded on fallback provider %s", m)
            return resp.choices[0].message.content or ""
        except _RETRYABLE as err:
            last_err = err
            _log.warning("provider %s failed after %d retries: %s", m, max_retries, str(err)[:120])
            continue

    assert last_err is not None
    raise last_err


@observe(name="llm-chat-stream")
def chat_stream(
    messages: list[dict],
    *,
    model: str = PRIMARY_MODEL,
    fallback_model: Optional[str] = FALLBACK_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    max_retries: int = 2,
    metadata: Optional[dict] = None,
    **kwargs: Any,
) -> Iterator[str]:
    """Yield completion tokens as they arrive. Tries each provider before the first token.

    Note: fallback can only happen before streaming begins; once tokens flow we
    can't switch providers mid-stream, so connection setup is where retries apply.
    """
    if metadata:
        kwargs.setdefault("metadata", metadata)

    emitted = 0  # tokens yielded so far — guards against mid-stream double-emit

    def _iter(m: str):
        nonlocal emitted
        resp = _attempt_model(
            m, messages, max_retries=max_retries, stream=True,
            temperature=temperature, max_tokens=max_tokens, **kwargs,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                emitted += 1
                yield delta

    chain = _model_chain(model, fallback_model)
    last_err: Optional[Exception] = None
    for i, m in enumerate(chain):
        if emitted:
            # Tokens already flowing — can't switch mid-stream.
            break
        if i > 0:
            cooldown = random.uniform(2, 6)
            _log.warning("stream: provider %s exhausted — waiting %.1fs then trying %s",
                         chain[i - 1], cooldown, m)
            time.sleep(cooldown)
        try:
            yield from _iter(m)
            return
        except _RETRYABLE as err:
            if emitted:
                raise  # already started — can't switch
            last_err = err
            _log.warning("stream: provider %s failed: %s", m, str(err)[:120])
            continue

    if last_err is not None:
        raise last_err


def health_check() -> dict:
    """Cheap reachability probe for all providers (used by /health)."""
    status = {}
    for label, m in (
        ("primary",    PRIMARY_MODEL),
        ("fallback",   FALLBACK_MODEL),
        ("fallback_2", FALLBACK_MODEL_2),
    ):
        try:
            chat(
                [{"role": "user", "content": "ping"}],
                model=m,
                fallback_model=None,
                max_tokens=1,
                max_retries=0,
            )
            status[label] = {"model": m, "ok": True}
        except Exception as err:  # pragma: no cover - network dependent
            status[label] = {"model": m, "ok": False, "error": str(err)[:200]}
    return status
