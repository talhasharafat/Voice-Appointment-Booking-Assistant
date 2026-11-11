from django.test import Client, SimpleTestCase, TestCase, override_settings


class VoiceUiTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_assistant_page_renders(self):
        response = self.client.get("/voice/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clinic")
        self.assertContains(response, "micButton")
        self.assertContains(response, "voice/assistant.js")
        self.assertContains(response, "livekit-client")
        self.assertContains(response, "tokenUrl")

    def test_root_redirects_to_assistant(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/voice/")

    @override_settings(LIVEKIT_API_KEY="devkey", LIVEKIT_API_SECRET="secret")
    def test_livekit_token_endpoint(self):
        response = self.client.post(
            "/voice/api/livekit/token/",
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["token"])
        self.assertTrue(body["room"])


class VoiceStaticContractTests(SimpleTestCase):
    def test_assistant_js_uses_livekit_not_cloud_stt(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "static" / "voice" / "assistant.js").read_text()
        self.assertIn("LivekitClient", js)
        self.assertIn("setMicrophoneEnabled", js)
        self.assertNotIn("api.openai.com", js.lower())
        self.assertNotIn("groq", js.lower())
