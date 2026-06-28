import unittest

from pwhl_shot_tracking.validation import automatic_flags, feed_shot_zone, finalize_rows


class ValidationTests(unittest.TestCase):
    def test_independent_check_flags_mismatch(self):
        shot = {
            "sync_status": "interpolated",
            "x": 100,
            "y": 150,
            "shooter_number": "29",
        }
        tag = {"shooter_number": "29"}
        verification = {
            "clip_valid": True,
            "shooter_number": "19",
            "shot_zone": "left_point",
            "shot_seen_at_seconds": 12,
        }
        flags = automatic_flags(shot, tag, verification, 14.0)
        self.assertIn("shooter_number_mismatch", flags)
        self.assertIn("shot_zone_mismatch", flags)
        self.assertEqual("low_slot", feed_shot_zone(shot))

    def test_finalize_requires_every_manual_label_and_thresholds(self):
        tagged = [
            {"shot_id": "a", "tag_royal_road": True},
            {"shot_id": "b", "tag_royal_road": False},
        ]
        manual = [
            {"shot_id": "a", "manual_disposition": "confirmed", "manual_royal_road": "true"},
            {"shot_id": "b", "manual_disposition": "confirmed", "manual_royal_road": "false"},
        ]
        rows, report = finalize_rows(tagged, manual, 0.85, 0.80)
        self.assertEqual("publishable", report["status"])
        self.assertTrue(report["thresholds_passed"])
        self.assertEqual([True, False], [row["final_royal_road"] for row in rows])


if __name__ == "__main__":
    unittest.main()
