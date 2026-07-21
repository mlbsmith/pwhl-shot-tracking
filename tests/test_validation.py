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
        errors, advisories = automatic_flags(shot, tag, verification, 14.0)
        # Without roster context the legacy strict behavior applies.
        self.assertIn("shooter_number_mismatch", errors)
        self.assertIn("shot_zone_mismatch", errors)
        self.assertEqual([], advisories)
        self.assertEqual("low_slot", feed_shot_zone(shot))

    def test_roster_context_separates_attribution_noise_from_sync_errors(self):
        rosters = {"2": {"13", "26"}, "3": {"88", "29"}}
        shot = {
            "sync_status": "interpolated",
            "x": 100,
            "y": 150,
            "shooter_number": "26",
            "team_id": "2",
        }
        tag = {"shooter_number": "26"}
        verification = {
            "clip_valid": True,
            "shooter_number": "13",
            "shot_zone": "low_slot",
            "shot_seen_at_seconds": 12,
        }
        # A teammate's number is attribution noise, not a sync error.
        errors, advisories = automatic_flags(shot, tag, verification, 14.0, rosters)
        self.assertEqual([], errors)
        self.assertIn("shooter_attribution_differs", advisories)
        # An opponent's number means the clip probably shows the wrong shot.
        verification["shooter_number"] = "88"
        errors, advisories = automatic_flags(shot, tag, verification, 14.0, rosters)
        self.assertIn("opposing_shooter_on_screen", errors)
        # A number on neither roster is most likely a misread jersey.
        verification["shooter_number"] = "99"
        errors, advisories = automatic_flags(shot, tag, verification, 14.0, rosters)
        self.assertEqual([], errors)
        self.assertIn("unrecognized_shooter_number", advisories)

    def test_adjacent_zone_families_are_advisory_not_error(self):
        shot = {
            "sync_status": "exact",
            "x": 100,
            "y": 150,
            "shooter_number": "26",
        }
        tag = {"shooter_number": "26"}
        verification = {
            "clip_valid": True,
            "shooter_number": "26",
            "shot_zone": "right_circle",
            "shot_seen_at_seconds": 12,
        }
        # Feed low_slot vs blind circle is broadcast-angle boundary noise.
        errors, advisories = automatic_flags(shot, tag, verification, 14.0)
        self.assertEqual([], errors)
        self.assertIn("shot_zone_differs_adjacent", advisories)
        # Two families apart is still an error.
        verification["shot_zone"] = "left_point"
        errors, _ = automatic_flags(shot, tag, verification, 14.0)
        self.assertIn("shot_zone_mismatch", errors)

    def test_royal_road_crossing_above_circle_tops_is_advisory(self):
        shot = {"sync_status": "exact", "x": 100, "y": 150, "shooter_number": "26"}
        tag = {
            "shooter_number": "26",
            "royal_road": True,
            "pass_geometry_confidence": "high",
            "pass_crossing_longitudinal_pct": 70,
        }
        verification = {
            "clip_valid": True,
            "shooter_number": "26",
            "shot_zone": "low_slot",
            "shot_seen_at_seconds": 12,
        }
        errors, advisories = automatic_flags(shot, tag, verification, 14.0)
        self.assertEqual([], errors)
        self.assertIn("royal_road_geometry_above_circles", advisories)

    def test_second_candidate_inside_clip_window_is_advisory(self):
        shot = {
            "sync_status": "exact",
            "x": 100,
            "y": 150,
            "shooter_number": "26",
            "clip_start_seconds": 100.0,
            "clip_end_seconds": 114.0,
        }
        tag = {"shooter_number": "26"}
        verification = {
            "clip_valid": True,
            "shooter_number": "26",
            "shot_zone": "low_slot",
            "shot_seen_at_seconds": 12,
        }
        errors, advisories = automatic_flags(
            shot, tag, verification, 14.0, None, [112.0]
        )
        self.assertEqual([], errors)
        self.assertIn("multi_shot_clip_window", advisories)
        _, advisories = automatic_flags(shot, tag, verification, 14.0, None, [130.0])
        self.assertNotIn("multi_shot_clip_window", advisories)

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
