"""
Free, keyless provider.

This project’s required voice path uses:
- browser Web Speech API for speech-to-text and text-to-speech (client-side)
- Django conversation + booking tools for dialogue and appointments

LocalProvider exists so get_provider() never needs a paid API. It does not call
external networks. Server-side STT/TTS are intentionally unsupported.
"""

from ai.exceptions import AIProviderError
from ai.providers.base import AIProvider, CompletionResult


class LocalProvider(AIProvider):
    name = "local"

    def complete(self, messages, tools=None, *, system_prompt=None, temperature=0.2):
        # Dialogue turns are driven by the free rule-based conversation engine.
        # Returning empty content keeps callers from inventing a paid-model reply.
        return CompletionResult(
            content="",
            tool_calls=[],
            provider=self.name,
            model="local-rules",
        )

    def transcribe(self, audio_bytes, *, mime_type="audio/webm", language="en"):
        raise AIProviderError(
            "Speech-to-text runs in the browser via the free Web Speech API. "
            "The local provider does not send audio to any paid service.",
            provider=self.name,
            retryable=False,
        )

    def synthesize(self, text, *, voice=None):
        raise AIProviderError(
            "Text-to-speech runs in the browser via free speechSynthesis. "
            "The local provider does not call any paid TTS API.",
            provider=self.name,
            retryable=False,
        )
