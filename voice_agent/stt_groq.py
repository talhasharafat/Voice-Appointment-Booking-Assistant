"""
Groq Whisper STT for LiveKit Agents (cloud — zero local GPU/CPU model load).
"""

from __future__ import annotations

import logging
import os

from livekit.plugins import groq

logger = logging.getLogger(__name__)

DEFAULT_STT_MODEL = "whisper-large-v3-turbo"

# Bias Whisper away from silence hallucinations / YouTube outros.
DEFAULT_STT_PROMPT = (
    "Clinic appointment booking conversation in English. "
    "Patient names, phone numbers, visit reasons, doctor names, dates and times. "
    "Ignore background noise. Do not invent speech."
)


def build_groq_stt():
    """STT swap point — Groq Whisper API."""
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is required for Groq Whisper STT. "
            "Create a free key at https://console.groq.com/keys"
        )
    model = (os.getenv("GROQ_STT_MODEL") or DEFAULT_STT_MODEL).strip() or DEFAULT_STT_MODEL
    language = (os.getenv("GROQ_STT_LANGUAGE") or "en").strip() or "en"
    prompt = (os.getenv("GROQ_STT_PROMPT") or DEFAULT_STT_PROMPT).strip()
    logger.info("Groq STT model=%s language=%s", model, language)
    return groq.STT(
        model=model,
        api_key=api_key,
        language=language,
        prompt=prompt,
    )
