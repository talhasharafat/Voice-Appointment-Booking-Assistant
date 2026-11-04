"""
Replaceable AI provider contract.

Scheduling and booking stay in the booking app. Voice session transport stays
in the voice app. Anything that talks to an external model or speech API must
implement AIProvider and be registered in ai.providers.get_provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChatMessage:
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List["ToolCall"]] = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class CompletionResult:
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    raw: Any = None

    @property
    def requested_tools(self):
        return bool(self.tool_calls)


class AIProvider(ABC):
    """Provider-agnostic surface used by conversation and voice layers."""

    name: str = "base"

    @abstractmethod
    def complete(
        self,
        messages,
        tools=None,
        *,
        system_prompt=None,
        temperature=0.2,
    ):
        """
        Run one model turn.

        Returns assistant text and/or structured tool_calls. The caller executes
        tools against Django services; the provider must not write appointments.
        """

    def transcribe(self, audio_bytes, *, mime_type="audio/webm", language="en"):
        """Speech-to-text. Optional for text-only providers."""
        raise NotImplementedError("{} does not support transcription.".format(self.name))

    def synthesize(self, text, *, voice=None):
        """Text-to-speech. Optional for text-only providers."""
        raise NotImplementedError("{} does not support speech synthesis.".format(self.name))
