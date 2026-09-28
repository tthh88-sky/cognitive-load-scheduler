"""LLM configuration, read entirely from LLM_-prefixed env vars so nothing
collides with Ollama's own server variables (e.g. OLLAMA_HOST). The Ollama
runbook (out of scope here) sets the values; this module only reads them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "ollama"  # LLM_PROVIDER: "ollama" or "none"
    host: str = "http://localhost:11434"  # LLM_HOST
    model: str = "qwen3.8:27b"  # LLM_MODEL
    allow_cloud: bool = False  # LLM_ALLOW_CLOUD, "1" means True
    timeout_s: float = 60.0  # LLM_TIMEOUT_S
    keep_alive: str = "30m"  # LLM_KEEP_ALIVE
    num_ctx: int = 8192  # LLM_NUM_CTX

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            provider=os.environ.get("LLM_PROVIDER", cls.provider),
            host=os.environ.get("LLM_HOST", cls.host),
            model=os.environ.get("LLM_MODEL", cls.model),
            allow_cloud=os.environ.get("LLM_ALLOW_CLOUD", "0") == "1",
            timeout_s=float(os.environ.get("LLM_TIMEOUT_S", cls.timeout_s)),
            keep_alive=os.environ.get("LLM_KEEP_ALIVE", cls.keep_alive),
            num_ctx=int(os.environ.get("LLM_NUM_CTX", cls.num_ctx)),
        )
