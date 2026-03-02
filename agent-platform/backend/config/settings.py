"""
Configuration layer — loads all runtime settings from environment variables.

Reference: ARCHITECTURE.md §3.7, §2.2
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Application ---
    app_name: str = "Agent Platform API"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    # --- LLM defaults ---
    default_low_cost_model: str = "gpt-4o-mini"
    default_high_reasoning_model: str = "gpt-4o"

    # --- Cost & token guardrails ---
    max_tokens_per_agent: int = 4096
    max_cost_per_request: float = 0.50
    request_timeout_seconds: int = 120

    # --- CORS (frontend origin) ---
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = {
        "env_file": os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".env"
        ),
        "env_file_encoding": "utf-8",
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor — cached after first call."""
    return Settings()
