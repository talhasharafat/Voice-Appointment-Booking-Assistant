"""Mint LiveKit access tokens and explicitly dispatch the local booking agent."""

from __future__ import annotations

import asyncio
import logging
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)


def mint_patient_token(room_name=None):
    """
    Create a unique room token and explicitly dispatch agent `clinic-booking`.

    Uses livekit-api so roomConfig / agent dispatch is encoded correctly.
    Also calls AgentDispatchService so the worker joins even if token
    roomConfig is ignored on an existing room.
    """
    api_key = getattr(settings, "LIVEKIT_API_KEY", "") or ""
    api_secret = getattr(settings, "LIVEKIT_API_SECRET", "") or ""
    url = getattr(settings, "LIVEKIT_URL", "ws://127.0.0.1:7880")
    agent_name = getattr(settings, "LIVEKIT_AGENT_NAME", "clinic-booking")

    if not api_key or not api_secret:
        raise RuntimeError(
            "LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set for the voice page."
        )

    room = room_name or "booking-{}".format(uuid.uuid4().hex[:10])
    identity = "patient-{}".format(uuid.uuid4().hex[:8])

    try:
        from livekit import api
    except ImportError as exc:
        raise RuntimeError(
            "Install livekit-api in the Django venv: pip install livekit-api"
        ) from exc

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name=agent_name)]
            )
        )
        .to_jwt()
    )

    # Explicit API dispatch (works even before the browser joins).
    try:
        asyncio.run(_create_dispatch(url, api_key, api_secret, room, agent_name))
    except Exception:  # noqa: BLE001
        logger.exception(
            "Agent dispatch API call failed for room=%s agent=%s "
            "(token roomConfig may still work)",
            room,
            agent_name,
        )

    return {
        "url": url,
        "token": token,
        "room": room,
        "identity": identity,
        "agent_name": agent_name,
    }


async def _create_dispatch(url, api_key, api_secret, room, agent_name):
    from livekit import api

    http_url = url.replace("ws://", "http://").replace("wss://", "https://")
    async with api.LiveKitAPI(http_url, api_key, api_secret) as lkapi:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=agent_name,
                room=room,
            )
        )
