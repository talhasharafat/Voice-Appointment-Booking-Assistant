"""
Groq Cloud LLM for LiveKit Agents.

Free-tier TPM is **per model** (~8k tokens/min). We use FallbackAdapter across
several models so a demo can continue when one bucket is exhausted.
"""

from __future__ import annotations

import logging
import os

from livekit.agents.llm import FallbackAdapter
from livekit.plugins import groq

logger = logging.getLogger(__name__)

# Primary + failover chain (separate Groq TPM buckets).
DEFAULT_LLM_MODELS = (
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
)


def groq_configured() -> bool:
    return bool((os.getenv("GROQ_API_KEY") or "").strip())


def _model_list() -> list[str]:
    raw = (os.getenv("GROQ_LLM_MODELS") or "").strip()
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models
    single = (os.getenv("GROQ_LLM_MODEL") or "").strip()
    if single:
        # Keep failover peers even when a primary is pinned.
        rest = [m for m in DEFAULT_LLM_MODELS if m != single]
        return [single, *rest]
    return list(DEFAULT_LLM_MODELS)


def _max_tokens(default: int = 120) -> int:
    try:
        return int(os.getenv("GROQ_LLM_MAX_TOKENS") or default)
    except ValueError:
        return default


def _build_one(model: str, *, temperature: float, max_tokens: int):
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        # Fail this instance quickly so FallbackAdapter can jump to the next TPM bucket.
        "max_retries": 1,
    }
    # gpt-oss defaults to reasoning_effort=low in the plugin; keep it.
    # Qwen thinking models need reasoning disabled for short voice turns.
    if model.startswith("qwen/"):
        kwargs["reasoning_effort"] = "none"
    return groq.LLM(**kwargs)


def build_groq_llm(*, temperature: float = 0.2, max_completion_tokens: int = 120):
    """
    LLM swap point — Groq Cloud with automatic model failover on 429 / errors.
    """
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is required for Groq LLM. "
            "Create a free key at https://console.groq.com/keys"
        )
    models = _model_list()
    max_tokens = _max_tokens(max_completion_tokens)
    instances = [
        _build_one(model, temperature=temperature, max_tokens=max_tokens) for model in models
    ]
    logger.info(
        "Groq LLM failover models=%s temperature=%s max_completion_tokens=%s",
        models,
        temperature,
        max_tokens,
    )
    # attempt_timeout: don't sit on a 429; move to next model (its own TPM budget).
    return FallbackAdapter(
        instances,
        attempt_timeout=float(os.getenv("GROQ_LLM_ATTEMPT_TIMEOUT", "6") or 6),
        max_retry_per_llm=0,
        retry_interval=0.3,
        sticky=False,
    )
