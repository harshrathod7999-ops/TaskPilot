"""TaskPilot configuration (env-driven, free-tier defaults)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py lives at 2-taskpilot/backend/app/config.py:
#   parents[1] = 2-taskpilot/backend   parents[2] = 2-taskpilot
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_PROJECT_ROOT / ".env", _BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Models (3-model chain: Groq → Gemini Flash → Gemini Flash Lite) ---
    primary_model: str = "groq/openai/gpt-oss-120b"
    fallback_model: str = "gemini/gemini-3.5-flash"
    fallback_model_2: str = "gemini/gemini-3.1-flash-lite"

    # --- Agent limits (the safety rails that make it portfolio-grade) ---
    agent_max_steps: int = 8
    agent_max_critiques: int = 2
    tool_timeout_seconds: int = 20

    # --- API ---
    taskpilot_api_port: int = 8001
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- MCP server ---
    taskpilot_db: str = "tasks.db"

    # --- Agent durability + run history (separate file from the MCP-owned
    # tasks.db — different writers, kept apart so neither schema leaks into
    # the other). Holds LangGraph's AsyncSqliteSaver checkpoints (graph.py)
    # and the runs/run_events tables (runs_store.py).
    agent_db: str = "agent.db"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def backend_root(self) -> Path:
        return _BACKEND_ROOT

    @property
    def db_path(self) -> Path:
        p = Path(self.taskpilot_db)
        return p if p.is_absolute() else _BACKEND_ROOT / p

    @property
    def agent_db_path(self) -> Path:
        p = Path(self.agent_db)
        return p if p.is_absolute() else _BACKEND_ROOT / p

    @property
    def mcp_server_script(self) -> Path:
        return _BACKEND_ROOT / "mcp_server" / "server.py"


@lru_cache
def get_settings() -> Settings:
    return Settings()
