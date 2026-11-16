"""
DeepSeek LLM for the LiveKit clinic booking agent.

Uses the OpenAI-compatible DeepSeek API via ``livekit.plugins.openai``.
Thinking/reasoning is disabled for low-latency voice turns with tool calling.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from livekit.plugins import openai

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_COMPLETION_TOKENS = 256


def deepseek_api_key() -> str:
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def deepseek_configured() -> bool:
    return bool(deepseek_api_key())


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_deepseek_llm(
    *,
    temperature: Optional[float] = None,
    model: Optional[str] = None,
):
    """
    Build the DeepSeek chat LLM used by AgentSession.

    Non-thinking mode is required for real-time voice: thinking adds multi-second
    latency and complicates tool-call multi-turn state.
    """
    api_key = deepseek_api_key()
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is required. Add it to .env "
            "(https://platform.deepseek.com)."
        )

    resolved_model = (model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip()
    resolved_temperature = (
        temperature
        if temperature is not None
        else _env_float("DEEPSEEK_TEMPERATURE", DEFAULT_TEMPERATURE)
    )

    max_completion_tokens = _env_int(
        "DEEPSEEK_MAX_COMPLETION_TOKENS",
        DEFAULT_MAX_COMPLETION_TOKENS,
    )

    logger.info(
        "DeepSeek LLM model=%s base_url=%s temperature=%s max_completion_tokens=%s thinking=disabled",
        resolved_model,
        base_url,
        resolved_temperature,
        max_completion_tokens,
    )

    return openai.LLM(
        model=resolved_model,
        api_key=api_key,
        base_url=base_url,
        temperature=resolved_temperature,
        max_completion_tokens=max_completion_tokens,
        tool_choice="auto",
        # Voice booking needs immediate tool decisions, not chain-of-thought.
        extra_body={"thinking": {"type": "disabled"}},
    )
