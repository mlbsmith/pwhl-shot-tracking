import tempfile
import unittest
from pathlib import Path

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.pipeline import sidecar_is_current, tag_shots
from pwhl_shot_tracking.utils import write_json


def _complete_sidecar(start, end):
    return {
        "status": "complete",
        "offsets": {"start_seconds": start, "end_seconds": end},
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
