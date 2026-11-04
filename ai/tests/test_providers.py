from django.test import SimpleTestCase, override_settings

from ai.exceptions import AIConfigurationError, AIProviderError
from ai.providers import ChatMessage, available_providers, get_provider
from ai.providers.fake_provider import FakeProvider
from ai.providers.local_provider import LocalProvider


class ProviderRegistryTests(SimpleTestCase):
    def test_available_providers_are_free_only(self):
        self.assertEqual(available_providers(), ["fake", "local"])

    @override_settings(AI_PROVIDER="local")
    def test_get_provider_defaults_to_local(self):
        provider = get_provider()
        self.assertIsInstance(provider, LocalProvider)

    @override_settings(AI_PROVIDER="fake")
    def test_get_provider_can_select_fake(self):
        self.assertIsInstance(get_provider(), FakeProvider)

    def test_unknown_provider_raises(self):
        with self.assertRaises(AIConfigurationError):
            get_provider(name="openai")


class LocalProviderTests(SimpleTestCase):
    def test_complete_stays_offline(self):
        result = LocalProvider().complete([ChatMessage(role="user", content="hi")])
        self.assertEqual(result.provider, "local")
        self.assertEqual(result.tool_calls, [])

    def test_speech_is_browser_only(self):
        provider = LocalProvider()
        with self.assertRaises(AIProviderError):
            provider.transcribe(b"x")
        with self.assertRaises(AIProviderError):
            provider.synthesize("hi")


class FakeProviderTests(SimpleTestCase):
    def test_scripted_result(self):
        provider = FakeProvider(scripted_results=[{"content": "ok", "tool_calls": []}])
        result = provider.complete([ChatMessage(role="user", content="hi")])
        self.assertEqual(result.content, "ok")
