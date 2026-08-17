"""Bridge to the vendored LiteLLM router (``shared/llm.py``).

TaskPilot ships its own copy of ``shared/`` inside the backend so the project
runs standalone. Falls back to a direct LiteLLM call only if ``shared`` somehow
isn't importable.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    from shared.llm import chat, chat_stream  # noqa: F401
except Exception:  # pragma: no cover - standalone fallback
    from litellm import completion

    def chat(messages, *, model="groq/openai/gpt-oss-120b", temperature=0.2,
             max_tokens=1024, **kwargs):
        resp = completion(model=model, messages=messages,
                          temperature=temperature, max_tokens=max_tokens)
        return resp.choices[0].message.content or ""

    def chat_stream(messages, *, model="groq/openai/gpt-oss-120b", temperature=0.2,
                    max_tokens=1024, **kwargs):
        resp = completion(model=model, messages=messages, stream=True,
                          temperature=temperature, max_tokens=max_tokens)
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
