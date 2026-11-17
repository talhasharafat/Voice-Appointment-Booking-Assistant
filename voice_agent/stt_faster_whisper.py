"""
Local Faster-Whisper STT for the LiveKit clinic voice agent.

Uses LiveKit's non-streaming STT interface; AgentSession + Silero VAD wraps it
via StreamAdapter automatically when streaming is not advertised.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

import numpy as np
from livekit.agents import stt as lk_stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer

from voice_agent.stt_filters import is_hallucinated_transcript

logger = logging.getLogger(__name__)

_model = None
_model_name: Optional[str] = None
TARGET_SR = 16000

# Short clinic vocabulary — helps Whisper lock onto confirmations / names.
# Kept tiny so it does not echo as fake patient speech.
_INITIAL_PROMPT = (
    "Yes. No. Okay. Phone number. Appointment. Doctor. "
    "Dermatology. General Practice."
)


def _load_model():
    global _model, _model_name
    name = (os.getenv("WHISPER_MODEL_SIZE") or "base").strip() or "base"
    if _model is not None and _model_name == name:
        return _model
    from faster_whisper import WhisperModel

    device = (os.getenv("WHISPER_DEVICE") or "cpu").strip() or "cpu"
    compute = (os.getenv("WHISPER_COMPUTE_TYPE") or "int8").strip() or "int8"
    logger.info("Loading Faster-Whisper model=%s device=%s compute=%s", name, device, compute)
    _model = WhisperModel(name, device=device, compute_type=compute)
    _model_name = name
    return _model


def _frames_from_buffer(buffer: AudioBuffer):
    if buffer is None:
        return []
    if isinstance(buffer, list):
        return buffer
    return [buffer]


def _audio_float32_16k(frames) -> np.ndarray:
    if not frames:
        return np.zeros(0, dtype=np.float32)

    sample_rate = int(getattr(frames[0], "sample_rate", TARGET_SR) or TARGET_SR)
    channels = int(getattr(frames[0], "num_channels", 1) or 1)
    pcm = np.concatenate(
        [np.frombuffer(bytes(f.data), dtype=np.int16) for f in frames]
    )
    if channels > 1:
        pcm = pcm.reshape(-1, channels)[:, 0]

    audio = pcm.astype(np.float32) / 32768.0
    if sample_rate == TARGET_SR or audio.size == 0:
        return audio

    duration = audio.size / float(sample_rate)
    target_len = max(1, int(round(duration * TARGET_SR)))
    x_old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _normalize_transcript(text: str) -> str:
    """Clean Whisper punctuation / spacing without changing meaning."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t).strip()
    # Collapse "Yes ?" / "Yes ." style STT artifacts for display + confirm parsing.
    t = re.sub(r"\s+([?!.])", r"\1", t)
    return t


def _beam_size() -> int:
    raw = (os.getenv("WHISPER_BEAM_SIZE") or "5").strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 5


class FasterWhisperSTT(lk_stt.STT):
    def __init__(self) -> None:
        super().__init__(
            capabilities=lk_stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._language = (os.getenv("WHISPER_LANGUAGE") or "en").strip() or "en"

    @property
    def model(self) -> str:
        return _model_name or (os.getenv("WHISPER_MODEL_SIZE") or "base")

    @property
    def provider(self) -> str:
        return "faster-whisper"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options=None,
    ) -> lk_stt.SpeechEvent:
        frames = _frames_from_buffer(buffer)
        audio = _audio_float32_16k(frames)
        # Allow short "yes"/"no" (was 150ms — too aggressive and dropped confirms).
        min_sec = float(os.getenv("WHISPER_MIN_AUDIO_SECONDS") or "0.08")
        if audio.size < TARGET_SR * max(0.05, min_sec):
            return lk_stt.SpeechEvent(
                type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[]
            )

        lang = language if language != NOT_GIVEN and language else self._language
        model = await asyncio.to_thread(_load_model)
        beam = _beam_size()

        def _transcribe():
            segments, info = model.transcribe(
                audio,
                language=lang,
                beam_size=beam,
                best_of=beam,
                patience=1.0,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 350},
                condition_on_previous_text=False,
                initial_prompt=_INITIAL_PROMPT,
                no_speech_threshold=0.55,
                compression_ratio_threshold=2.6,
                log_prob_threshold=-1.0,
                temperature=[0.0, 0.2, 0.4],
            )
            if getattr(info, "language_probability", 1.0) < 0.35:
                return ""
            parts = []
            for seg in segments:
                t = (seg.text or "").strip()
                if not t:
                    continue
                if getattr(seg, "no_speech_prob", 0.0) > 0.7:
                    continue
                # Keep short confirmations even if confidence is middling.
                avg = getattr(seg, "avg_logprob", 0.0)
                if len(t) <= 8 and t.lower().strip(" ?.!") in {
                    "yes",
                    "yeah",
                    "yep",
                    "yup",
                    "no",
                    "nope",
                    "ok",
                    "okay",
                }:
                    parts.append(t)
                    continue
                if avg < -1.2:
                    continue
                parts.append(t)
            return _normalize_transcript(" ".join(parts))

        text = await asyncio.to_thread(_transcribe)
        if not text or is_hallucinated_transcript(text):
            if text:
                logger.info("faster-whisper dropped hallucinated text=%r", text[:120])
            return lk_stt.SpeechEvent(
                type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[]
            )

        logger.info("faster-whisper text=%r", text[:160])
        return lk_stt.SpeechEvent(
            type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[lk_stt.SpeechData(language=lang, text=text, confidence=0.0)],
        )


def build_faster_whisper_stt():
    _load_model()
    return FasterWhisperSTT()
