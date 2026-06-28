import json
import unittest

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.gemini import GeminiClient, TAG_SCHEMA


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class GeminiTests(unittest.TestCase):
    def test_generate_uses_clipping_fps_schema_and_header_key(self):
        config = dict(DEFAULT_CONFIG)
        config["video_url"] = "https://www.youtube.com/watch?v=test"
        observed = {}
        structured = {
            "royal_road": False,
            "royal_road_confidence": "high",
        }

        def opener(request, timeout):
            observed["request"] = request
            observed["timeout"] = timeout
            return FakeResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": json.dumps(structured)}]
                            }
                        }
                    ]
                }
            )

        client = GeminiClient(config, api_keys=["secret"], opener=opener, sleeper=lambda _: None)
        result, audit = client.generate("prompt", TAG_SCHEMA, 10.0, 24.0)
        body = json.loads(observed["request"].data.decode("utf-8"))
        video = body["contents"][0]["parts"][0]
        self.assertEqual("10s", video["videoMetadata"]["startOffset"])
        self.assertEqual("24s", video["videoMetadata"]["endOffset"])
        self.assertEqual(8.0, video["videoMetadata"]["fps"])
        self.assertEqual(TAG_SCHEMA, body["generationConfig"]["responseJsonSchema"])
        self.assertEqual("secret", observed["request"].get_header("X-goog-api-key"))
        self.assertNotIn("secret", json.dumps(audit))
        self.assertEqual(structured, result)


if __name__ == "__main__":
    unittest.main()
