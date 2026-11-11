"""
Fish Audio TTS for LiveKit Agents.

Uses Fish's OpenAI-compatible ``/v1/audio/speech`` with ``stream_format=audio``.

Important: Fish's default PCM is 44.1 kHz. If LiveKit is told 24 kHz while receiving
44.1 kHz PCM, speech plays slow/robotic. We therefore use ``mp3`` (or ``wav``) so
LiveKit decodes the real rate and resamples cleanly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from typing import Any, Optional

import httpx
import openai
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.plugins.openai.tts import (
    DECODABLE_CONTENT_TYPES,
    NUM_CHANNELS,
    TTS as OpenAITTS,
)

logger = logging.getLogger(__name__)

DEFAULT_FISH_BASE = "https://api.fish.audio/compat/v1"
DEFAULT_FISH_MODEL = "fish-audio/s2.1-pro-free"
# Output sample rate after decode inside LiveKit (mp3/wav are resampled to this).
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_RESPONSE_FORMAT = "mp3"


class _FishAudioChunkedStream(tts.ChunkedStream):
    """OpenAI TTS stream that always requests Fish-compatible raw audio (not SSE)."""

    def __init__(self, *, tts_inst: OpenAITTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_inst, input_text=input_text, conn_options=conn_options)
        self._tts: OpenAITTS = tts_inst
        self._opts = replace(tts_inst._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        fmt = self._opts.response_format
        create_kwargs: dict[str, Any] = {
            "input": self.input_text,
            "model": self._opts.model,
            "voice": self._opts.voice,
            "response_format": fmt,
            "speed": self._opts.speed,
            # Fish rejects instructions with 400.
            "instructions": openai.omit,
            "stream_format": "audio",
            "timeout": httpx.Timeout(45, connect=self._conn_options.timeout),
        }
        # Only needed for raw PCM; encoded formats carry their own timing.
        if fmt in {"pcm", "pcm16"}:
            create_kwargs["extra_body"] = {"sample_rate": self._tts.sample_rate}

        oai_stream = self._tts._client.audio.speech.with_streaming_response.create(
            **create_kwargs
        )

        try:
            async with oai_stream as stream:
                media_type = stream.headers.get("content-type", "").split(";")[0].strip().lower()
                mime_type = (
                    media_type
                    if media_type in DECODABLE_CONTENT_TYPES
                    else f"audio/{fmt}"
                )
                output_emitter.initialize(
                    request_id=stream.request_id or "",
                    sample_rate=self._tts.sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type=mime_type,
                )
                async for chunk in stream.iter_bytes():
                    output_emitter.push(chunk)
                output_emitter.flush()
        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message,
                status_code=e.status_code,
                request_id=e.request_id,
                body=e.body,
            ) from None
        except Exception as e:
            raise APIConnectionError() from e


class FishAudioTTS(OpenAITTS):
    """OpenAI-compatible Fish TTS that never requests SSE."""

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return _FishAudioChunkedStream(tts_inst=self, input_text=text, conn_options=conn_options)


def build_fish_tts(*, voice: Any = None) -> FishAudioTTS:
    """TTS swap point — Fish Audio free/pro models via OpenAI-compatible API."""
    api_key = (os.getenv("FISH_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "FISH_API_KEY is required. Create a free Fish Audio key at "
            "https://fish.audio and set FISH_API_KEY in .env"
        )
    base_url = (os.getenv("FISH_BASE_URL") or DEFAULT_FISH_BASE).strip() or DEFAULT_FISH_BASE
    model = (os.getenv("FISH_TTS_MODEL") or DEFAULT_FISH_MODEL).strip() or DEFAULT_FISH_MODEL
    voice_id = (
        voice
        if isinstance(voice, str) and voice.strip()
        else (os.getenv("FISH_VOICE_ID") or "").strip()
    )
    if voice_id.lower() in {"default", "none", "null"}:
        voice_id = ""
    sample_rate = int(os.getenv("FISH_SAMPLE_RATE") or DEFAULT_SAMPLE_RATE)
    response_format = (
        os.getenv("FISH_RESPONSE_FORMAT") or DEFAULT_RESPONSE_FORMAT
    ).strip().lower() or DEFAULT_RESPONSE_FORMAT
    if response_format not in {"mp3", "wav", "opus", "pcm", "pcm16"}:
        response_format = DEFAULT_RESPONSE_FORMAT
    logger.info(
        "Fish Audio TTS model=%s format=%s sample_rate=%s voice=%r",
        model,
        response_format,
        sample_rate,
        voice_id or "(default)",
    )
    return FishAudioTTS(
        model=model,
        voice=voice_id,
        api_key=api_key,
        base_url=base_url,
        response_format=response_format,
        sample_rate=sample_rate,
    )


def load_fish_tts_config() -> Optional[dict]:
    """Validate env without calling the network."""
    api_key = (os.getenv("FISH_API_KEY") or "").strip()
    if not api_key:
        return None
    return {
        "model": (os.getenv("FISH_TTS_MODEL") or DEFAULT_FISH_MODEL).strip(),
        "base_url": (os.getenv("FISH_BASE_URL") or DEFAULT_FISH_BASE).strip(),
        "response_format": (
            os.getenv("FISH_RESPONSE_FORMAT") or DEFAULT_RESPONSE_FORMAT
        ).strip(),
    }
