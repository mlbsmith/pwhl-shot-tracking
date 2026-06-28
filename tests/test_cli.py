import argparse
import tempfile
import unittest
from pathlib import Path

from pwhl_shot_tracking.cli import command_finalize
from pwhl_shot_tracking.config import create_config, load_config, work_path
from pwhl_shot_tracking.utils import read_json, write_csv, write_json


class CliTests(unittest.TestCase):
    def test_finalize_blocks_publish_when_a_denominator_candidate_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            create_config(
                config_path,
                "1",
                "https://www.youtube.com/watch?v=test",
                "shots_on_goal",
            )
            config = load_config(config_path)
            write_csv(
                work_path(config, "shots.csv"),
                [{"shot_id": "a"}, {"shot_id": "b"}],
            )
            write_csv(
                work_path(config, "royal_road_1.csv"),
                [{"shot_id": "a", "tag_royal_road": True}],
            )
            write_csv(
                work_path(config, "manual_review.csv"),
                [
                    {
                        "shot_id": "a",
                        "manual_disposition": "confirmed",
                        "manual_royal_road": "true",
                        "manual_notes": "checked",
                    }
                ],
            )
            sidecar_path = work_path(config, "clips", "a.json")
            write_json(sidecar_path, {"status": "complete", "manual_disposition": "pending"})

            result = command_finalize(argparse.Namespace(config=str(config_path), review=None))
            report = read_json(work_path(config, "validation_report.json"))
            sidecar = read_json(sidecar_path)

        self.assertEqual(1, result)
        self.assertFalse(report["pipeline_complete"])
        self.assertEqual(["b"], report["missing_shot_ids"])
        self.assertFalse(report["thresholds_passed"])
        self.assertEqual("confirmed", sidecar["manual_disposition"])
        self.assertTrue(sidecar["final_royal_road"])


if __name__ == "__main__":
    unittest.main()
