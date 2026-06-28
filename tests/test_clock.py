import unittest

from pwhl_shot_tracking.clock import Anchor, assign_active_runs, map_clock_to_video, synchronize_shots
from pwhl_shot_tracking.config import DEFAULT_CONFIG


class ClockTests(unittest.TestCase):
    def test_interpolates_only_inside_active_clock_run(self):
        anchors = assign_active_runs(
            [
                Anchor(1, 1190, 100.0),
                Anchor(1, 1180, 110.0),
                Anchor(1, 1170, 130.0),
                Anchor(1, 1160, 140.0),
            ],
            max_stoppage_gap_seconds=3.0,
        )
        mapped, status, run_id = map_clock_to_video(anchors, 1, 1185)
        self.assertEqual(105.0, mapped)
        self.assertEqual("interpolated", status)
        self.assertTrue(run_id)
        mapped, status, _ = map_clock_to_video(anchors, 1, 1175)
        self.assertIsNone(mapped)
        self.assertEqual("unmapped", status)

    def test_sync_builds_offsets_and_review_link(self):
        config = dict(DEFAULT_CONFIG)
        config["video_url"] = "https://www.youtube.com/watch?v=test"
        anchors = [
            Anchor(1, 1100, 200.0, run_id="one"),
            Anchor(1, 1090, 210.0, run_id="one"),
        ]
        rows, report = synchronize_shots(
            [{"shot_id": "shot-1", "period": 1, "remaining_seconds": 1095}],
            anchors,
            config,
        )
        self.assertEqual(193.0, rows[0]["clip_start_seconds"])
        self.assertEqual(207.0, rows[0]["clip_end_seconds"])
        self.assertIn("t=205s", rows[0]["review_url"])
        self.assertEqual({"interpolated": 1}, report["sync_status_counts"])


if __name__ == "__main__":
    unittest.main()
