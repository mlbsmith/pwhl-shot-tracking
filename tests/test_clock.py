import unittest

from pwhl_shot_tracking.clock import (
    Anchor,
    assign_active_runs,
    map_clock_to_video,
    merge_anchors,
    sync_gap_report,
    synchronize_shots,
)
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

    def test_repeat_reading_of_same_displayed_second_stays_in_one_run(self):
        anchors = assign_active_runs(
            [
                Anchor(1, 1170, 100.0),
                Anchor(1, 1170, 100.9),
                Anchor(1, 1160, 110.9),
            ],
            max_stoppage_gap_seconds=3.0,
        )
        self.assertEqual(1, len({anchor.run_id for anchor in anchors}))
        # A stalled clock across a longer gap is still a new run.
        anchors = assign_active_runs(
            [Anchor(1, 1170, 100.0), Anchor(1, 1170, 106.0)],
            max_stoppage_gap_seconds=3.0,
        )
        self.assertEqual(2, len({anchor.run_id for anchor in anchors}))

    def test_merge_anchors_deduplicates_and_prefers_manual_run_ids(self):
        existing = [
            Anchor(1, 1170, 100.0, source="gemini", run_id="p1-run-001"),
            Anchor(1, 1160, 110.2, source="manual", run_id="opening"),
        ]
        discovered = [
            Anchor(1, 1170, 100.4, source="gemini", run_id="p1-run-009"),
            Anchor(1, 1150, 120.0, source="gemini"),
        ]
        merged = merge_anchors(existing, discovered)
        self.assertEqual(3, len(merged))
        self.assertEqual([100.0, 110.2, 120.0], [anchor.video_seconds for anchor in merged])
        # Discovered run ids are stale across scans and must be reassigned.
        self.assertEqual(["", "opening", ""], [anchor.run_id for anchor in merged])

    def test_gap_report_brackets_unmapped_shots_and_merges_scan_windows(self):
        anchors = [
            Anchor(1, 1100, 200.0, run_id="one"),
            Anchor(1, 1090, 210.0, run_id="one"),
            Anchor(1, 900, 500.0, run_id="two"),
            Anchor(1, 890, 510.0, run_id="two"),
        ]
        rows = [
            {"shot_id": "shot-a", "sync_status": "unmapped", "period": 1, "remaining_seconds": 1000},
            {"shot_id": "shot-b", "sync_status": "unmapped", "period": 1, "remaining_seconds": 950},
            {"shot_id": "shot-c", "sync_status": "unmapped", "period": 1, "remaining_seconds": 60},
            {"shot_id": "shot-d", "sync_status": "interpolated", "period": 1, "remaining_seconds": 1095},
        ]
        report = sync_gap_report(rows, anchors, video_duration_seconds=1200.0, pad_seconds=10.0)
        self.assertEqual(3, report["unmapped_count"])
        by_id = {gap["shot_id"]: gap for gap in report["gaps"]}
        # Bracketed by anchors on both sides: window spans the bounding anchors.
        self.assertEqual("18:10", by_id["shot-a"]["anchor_before"]["game_clock"])
        self.assertEqual("15:00", by_id["shot-a"]["anchor_after"]["game_clock"])
        self.assertEqual(200.0, by_id["shot-a"]["scan_start_seconds"])
        self.assertEqual(510.0, by_id["shot-a"]["scan_end_seconds"])
        # Unbounded after: clock distance plus slack, clamped to the VOD duration.
        self.assertIsNone(by_id["shot-c"]["anchor_after"])
        self.assertEqual(500.0, by_id["shot-c"]["scan_start_seconds"])
        self.assertEqual(1200.0, by_id["shot-c"]["scan_end_seconds"])
        # shot-a and shot-b share a window; shot-c overlaps it and merges too.
        self.assertEqual(1, len(report["suggested_scans"]))
        self.assertEqual(
            ["shot-a", "shot-b", "shot-c"],
            sorted(report["suggested_scans"][0]["shot_ids"]),
        )
        self.assertIn("discover-anchors", report["suggested_scans"][0]["command"])

    def test_gap_report_handles_inverted_and_anchorless_periods(self):
        anchors = [
            # Clock order disagrees with video order (a bad anchor): the
            # window must still be ascending and cover both sightings.
            Anchor(1, 1100, 500.0, run_id="one"),
            Anchor(1, 900, 200.0, run_id="two"),
            Anchor(3, 1100, 3000.0, run_id="three"),
        ]
        rows = [
            {"shot_id": "inverted", "sync_status": "unmapped", "period": 1, "remaining_seconds": 1000},
            {"shot_id": "no-anchors", "sync_status": "unmapped", "period": 2, "remaining_seconds": 600},
        ]
        report = sync_gap_report(rows, anchors, video_duration_seconds=4000.0, pad_seconds=10.0)
        by_id = {gap["shot_id"]: gap for gap in report["gaps"]}
        self.assertEqual(190.0, by_id["inverted"]["scan_start_seconds"])
        self.assertEqual(510.0, by_id["inverted"]["scan_end_seconds"])
        # A period with no anchors is bracketed by its neighbouring periods.
        self.assertEqual(490.0, by_id["no-anchors"]["scan_start_seconds"])
        self.assertEqual(3010.0, by_id["no-anchors"]["scan_end_seconds"])

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
