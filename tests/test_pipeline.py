import tempfile
import unittest
from pathlib import Path

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.pipeline import sidecar_is_current, tag_shots
from pwhl_shot_tracking.utils import write_json

GEMINI = DEFAULT_CONFIG["gemini"]


def _complete_sidecar(start, end, feed_sha256="hash"):
    return {
        "status": "complete",
        "offsets": {"start_seconds": start, "end_seconds": end},
        "feed_snapshot_sha256": feed_sha256,
        "model_id": GEMINI["model"],
        "fps": GEMINI["fps"],
        "media_resolution": GEMINI["media_resolution"],
        "passes": {
            "contextual": {"parsed_response": {"royal_road": True, "shooter_number": "9"}},
            "blind_verification": {
                "parsed_response": {
                    "clip_valid": True,
                    "shooter_number": "9",
                    "shot_zone": "low_slot",
                    "shot_seen_at_seconds": 12,
                }
            },
        },
    }


class PipelineCacheTests(unittest.TestCase):
    def test_sidecar_is_current_requires_matching_offsets(self):
        audit = _complete_sidecar(100.0, 114.0)
        self.assertTrue(sidecar_is_current(audit, 100.0, 114.0))
        self.assertTrue(sidecar_is_current(audit, 100.2, 114.2))
        # A re-sync that moved the clip must invalidate the cache.
        self.assertFalse(sidecar_is_current(audit, 103.0, 117.0))
        self.assertFalse(sidecar_is_current({"status": "complete"}, 100.0, 114.0))
        self.assertFalse(sidecar_is_current(_complete_sidecar(100.0, 114.0) | {"status": "failed"}, 100.0, 114.0))

    def test_sidecar_is_current_requires_matching_identity_and_responses(self):
        audit = _complete_sidecar(100.0, 114.0)
        self.assertTrue(sidecar_is_current(audit, 100.0, 114.0, "hash", GEMINI))
        # A refreshed feed or changed Gemini configuration invalidates it.
        self.assertFalse(sidecar_is_current(audit, 100.0, 114.0, "other-hash", GEMINI))
        self.assertFalse(
            sidecar_is_current(audit, 100.0, 114.0, "hash", dict(GEMINI, fps=1.0))
        )
        self.assertFalse(
            sidecar_is_current(audit, 100.0, 114.0, "hash", dict(GEMINI, model="other-model"))
        )
        # A truncated sidecar (complete but missing a pass) is not reusable,
        # so the call-count guard and the cache agree.
        truncated = _complete_sidecar(100.0, 114.0)
        del truncated["passes"]["blind_verification"]
        self.assertFalse(sidecar_is_current(truncated, 100.0, 114.0))

    def test_tag_shots_serves_matching_sidecars_without_a_client(self):
        shot = {
            "shot_id": "shot-1",
            "shooter_number": "9",
            "x": 100,
            "y": 150,
            "sync_status": "exact",
            "clip_start_seconds": 100.0,
            "clip_end_seconds": 114.0,
            "video_seconds": 112.0,
        }
        config = dict(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            clips = Path(directory)
            write_json(clips / "shot-1.json", _complete_sidecar(100.0, 114.0))
            tagged, report = tag_shots([shot], config, None, "hash", clips)
        self.assertEqual(1, report["cached_count"])
        self.assertEqual(0, report["api_call_count"])
        self.assertEqual(True, tagged[0]["tag_royal_road"])


if __name__ == "__main__":
    unittest.main()
