"""Agent settings from .env / the environment. The Groq key is read here and nowhere logged or printed."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# The official Filesystem MCP server (Anthropic / MCP maintainers). The version is pinned so a run is reproducible.
FILESYSTEM_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"

# Default models per provider (open-weights only). Why these two: see README "Agent architecture" and agent/llm.py.
DEFAULT_MODELS = {"groq": "qwen/qwen3.8-27b", "ollama": "qwen2.5:7b"}
PROVIDERS = tuple(DEFAULT_MODELS)


class SettingsError(RuntimeError):
    """A missing or invalid setting, with a message that never contains a secret."""


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    groq_api_key: str = field(default="", repr=False)       # repr=False: never shows up in logs or tracebacks
    ollama_base_url: str = "http://localhost:11434"
    outputs_dir: Path = ROOT / "outputs"                      # the only directory the Filesystem MCP server can touch
    logs_dir: Path = ROOT / "logs"


def load_settings(env_file: Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    """Read LLM_PROVIDER / MODEL / GROQ_API_KEY (and optional OLLAMA_BASE_URL) from .env, then the environment."""
    if environ is None:
        load_dotenv(env_file or ROOT / ".env", override=False)
        environ = dict(os.environ)
    provider = (environ.get("LLM_PROVIDER") or "groq").strip().lower()
    if provider not in PROVIDERS:
        raise SettingsError(f"LLM_PROVIDER must be one of {list(PROVIDERS)}, got {provider!r}.")
    model = (environ.get("MODEL") or "").strip() or DEFAULT_MODELS[provider]
    key = (environ.get("GROQ_API_KEY") or "").strip()
    if provider == "groq" and not key:
        raise SettingsError("LLM_PROVIDER=groq needs GROQ_API_KEY in .env (see .env.example).")
    return Settings(provider=provider, model=model, groq_api_key=key,
                    ollama_base_url=(environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").strip())
