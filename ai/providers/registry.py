"""
Provider registry — free tools only.

Default: AI_PROVIDER=local
Speech I/O: browser Web Speech API (SPEECH_BACKEND=browser)
Dialogue + booking: Django conversation engine and booking.services
"""

from django.conf import settings

from ai.exceptions import AIConfigurationError
from ai.providers.fake_provider import FakeProvider
from ai.providers.local_provider import LocalProvider

PROVIDER_CLASSES = {
    "local": LocalProvider,
    "fake": FakeProvider,
}


def available_providers():
    return sorted(PROVIDER_CLASSES.keys())


def get_provider(name=None, **kwargs):
    provider_name = (name or getattr(settings, "AI_PROVIDER", "local") or "local").strip().lower()
    try:
        provider_cls = PROVIDER_CLASSES[provider_name]
    except KeyError as exc:
        raise AIConfigurationError(
            "Unknown AI_PROVIDER {!r}. Available: {}.".format(
                provider_name,
                ", ".join(available_providers()),
            ),
            provider=provider_name,
        ) from exc
    return provider_cls(**kwargs)
