"""Publish structured UI cards to the browser over LiveKit data packets."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def publish_ui(event_type: str, **payload: Any) -> None:
    """Send a JSON UI event to the connected patient page."""
    try:
        from livekit.agents import get_job_context

        ctx = get_job_context(required=False)
        if ctx is None or ctx.room is None:
            return
        body = {"type": event_type, **payload}
        await ctx.room.local_participant.publish_data(
            json.dumps(body, ensure_ascii=False),
            reliable=True,
            topic="clinic.ui",
        )
    except Exception:  # noqa: BLE001
        logger.exception("publish_ui failed type=%s", event_type)
