from django.test import Client, SimpleTestCase, TestCase


class VoiceUiTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_assistant_page_renders(self):
        response = self.client.get("/voice/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clinic")
        self.assertContains(response, "micButton")
        self.assertContains(response, "voice/assistant.js")
        self.assertContains(response, "speechBackend")

    def test_root_redirects_to_assistant(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/voice/")


class VoiceStaticContractTests(SimpleTestCase):
    def test_assistant_js_uses_browser_speech_only(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "static" / "voice" / "assistant.js").read_text()
        self.assertIn("SpeechRecognition", js)
        self.assertIn("speechSynthesis", js)
        self.assertNotIn("openai", js.lower())
        self.assertNotIn("api.openai.com", js.lower())
