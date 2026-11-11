"""Clean LLM text before TTS/UI: strip think tags, markdown, stutters, runaway turns."""

from __future__ import annotations

import re
from typing import AsyncIterable, Union

from livekit.agents.types import TimedString

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_OPEN = "<think>"
_CLOSE = "</think>"
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_BULLET = re.compile(r"(?m)^\s*[-*•]\s+")
_ROLE_PLAY = re.compile(
    r"(?i)\b(sure[,.]?\s+my\s+phone|patient\s*:|you\s*:|user\s*:|"
    r"got\s+it\.\s+what(?:'s|\s+is)\s+the\s+brief|"
    r"perfect\.\s+what\s+date)"
)
_IMMEDIATE_DUP_WORD = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "")
    lower = cleaned.lower()
    idx = lower.find("<think>")
    if idx >= 0:
        cleaned = cleaned[:idx]
    return cleaned.strip()


def strip_markdown(text: str) -> str:
    cleaned = _MD_BOLD.sub(r"\1", text or "")
    cleaned = _MD_BULLET.sub("", cleaned)
    cleaned = cleaned.replace("`", "")
    return cleaned


def collapse_stutter(text: str) -> str:
    """Collapse 'When When would When would you like…' into one clean phrase."""
    cleaned = _IMMEDIATE_DUP_WORD.sub(r"\1", text or "")
    # Progressive restart stutter after a completed question.
    q = cleaned.find("?")
    if q >= 0:
        head = cleaned[: q + 1].strip()
        tail = cleaned[q + 1 :].strip()
        if not tail:
            return head
        # Keep only the longest progressive phrase in the tail.
        starts = [m.start() for m in re.finditer(r"(?i)\b(?:when|what|which|could|please)\b", tail)]
        if len(starts) >= 2:
            best = ""
            for start in starts:
                cand = tail[start:].strip()
                if len(cand) >= len(best):
                    best = cand
            return f"{head} {best}".strip()
        return f"{head} {tail}".strip()

    starts = [m.start() for m in re.finditer(r"(?i)\b(?:when|what|which|could|please)\b", cleaned)]
    if len(starts) >= 3:
        best = ""
        for start in starts:
            cand = cleaned[start:].strip()
            if len(cand) >= len(best):
                best = cand
        # Preserve any short lead-in before the stutter run.
        lead = cleaned[: starts[0]].strip()
        return f"{lead} {best}".strip() if lead else best
    return cleaned


def clamp_assistant_reply(text: str) -> str:
    """Keep one assistant beat; cut invented dialogue after the first '?'."""
    cleaned = collapse_stutter(strip_markdown(strip_think_tags(text or "")))
    if not cleaned:
        return cleaned

    role = _ROLE_PLAY.search(cleaned)
    if role and role.start() > 12:
        cleaned = cleaned[: role.start()].rstrip(" ,;:-")

    q = cleaned.find("?")
    if q >= 0:
        head = cleaned[: q + 1].strip()
        tail = cleaned[q + 1 :].strip()
        if "?" in tail or len(tail) > 24:
            return head
        if tail:
            return f"{head} {tail}".strip()
        return head
    return cleaned.strip()


async def strip_think_stream(
    text: AsyncIterable[Union[str, TimedString]],
) -> AsyncIterable[Union[str, TimedString]]:
    """Stream cleaner for TTS/transcription nodes."""
    think_buf = ""
    in_think = False
    raw = ""
    spoken = ""
    closed = False

    async for chunk in text:
        if closed:
            continue
        is_timed = isinstance(chunk, TimedString)
        think_buf += str(chunk)

        plain = ""
        while think_buf:
            if in_think:
                close_at = think_buf.lower().find(_CLOSE)
                if close_at < 0:
                    think_buf = ""
                    break
                think_buf = think_buf[close_at + len(_CLOSE) :]
                in_think = False
                continue

            open_at = think_buf.lower().find(_OPEN)
            if open_at < 0:
                hold = 0
                lower = think_buf.lower()
                for i in range(1, min(len(_OPEN), len(think_buf)) + 1):
                    if lower.endswith(_OPEN[:i]):
                        hold = i
                if hold:
                    plain += think_buf[:-hold]
                    think_buf = think_buf[-hold:]
                else:
                    plain += think_buf
                    think_buf = ""
                break

            plain += think_buf[:open_at]
            think_buf = think_buf[open_at + len(_OPEN) :]
            in_think = True

        if not plain:
            continue

        raw += plain
        clamped = clamp_assistant_reply(raw)
        if len(clamped) < len(spoken) or (
            spoken and not clamped.startswith(spoken[: max(0, len(spoken) - 12)])
            and not spoken.startswith(clamped[: max(0, len(clamped) - 12)])
        ):
            # Rewritten earlier text — stop rather than re-speak a garbled stream.
            closed = True
            continue
        if len(clamped) < len(spoken):
            closed = True
            continue
        delta = clamped[len(spoken) :]
        spoken = clamped
        if not delta:
            if "?" in spoken:
                closed = True
            continue
        if is_timed:
            yield TimedString(text=delta, start_time=getattr(chunk, "start_time", 0.0))
        else:
            yield delta
