"""Deterministic provider for local tests without network credentials."""

from ai.providers.base import AIProvider, CompletionResult, ToolCall


class FakeProvider(AIProvider):
    name = "fake"

    def __init__(self, scripted_results=None):
        self.scripted_results = list(scripted_results or [])
        self.calls = []

    def complete(self, messages, tools=None, *, system_prompt=None, temperature=0.2):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system_prompt": system_prompt,
                "temperature": temperature,
            }
        )
        if self.scripted_results:
            next_result = self.scripted_results.pop(0)
            if isinstance(next_result, CompletionResult):
                next_result.provider = self.name
                return next_result
            if isinstance(next_result, dict):
                tool_calls = [
                    ToolCall(
                        id=item.get("id", "call_{}".format(index)),
                        name=item["name"],
                        arguments=item.get("arguments", {}),
                    )
                    for index, item in enumerate(next_result.get("tool_calls", []))
                ]
                return CompletionResult(
                    content=next_result.get("content"),
                    tool_calls=tool_calls,
                    provider=self.name,
                    model="fake-model",
                )
            return CompletionResult(content=str(next_result), provider=self.name, model="fake-model")
        return CompletionResult(
            content="Fake provider has no scripted reply.",
            provider=self.name,
            model="fake-model",
        )

    def transcribe(self, audio_bytes, *, mime_type="audio/webm", language="en"):
        self.calls.append({"transcribe_bytes": len(audio_bytes), "mime_type": mime_type})
        return "hello my name is test patient"

    def synthesize(self, text, *, voice=None):
        self.calls.append({"synthesize": text, "voice": voice})
        return b"FAKE_AUDIO:" + text.encode("utf-8")
