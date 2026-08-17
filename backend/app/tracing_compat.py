"""Bridge to the vendored ``shared.tracing`` Langfuse helpers (standalone-safe)."""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    from shared.tracing import flush, get_client, observe, tracing_enabled  # noqa: F401
except Exception:  # pragma: no cover
    def observe(*, name=None):
        def deco(fn):
            return fn
        return deco

    def tracing_enabled() -> bool:
        return False

    def get_client():
        return None

    def flush() -> None:
        pass
