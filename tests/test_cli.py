import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pwhl_shot_tracking.cli import command_finalize, command_init
from pwhl_shot_tracking.config import create_config, load_config, work_path
from pwhl_shot_tracking.utils import read_json, write_csv, write_json


class CliTests(unittest.TestCase):
    def test_init_resolves_game_id_when_override_is_omitted(self):
        resolution = {
            "game_id": "340",
            "video": {"canonical_url": "https://www.youtube.com/watch?v=AbC_123-xY"},
            "selected": {
                "visiting_team": "Minnesota Frost",
                "home_team": "Montréal Victoire",
                "date_played": "2026-05-02",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            arguments = argparse.Namespace(
                config=str(config_path),
                game_id=None,
                video_url="https://youtu.be/AbC_123-xY",
                shot_universe="shots_on_goal",
                force=False,
            )
            with patch("pwhl_shot_tracking.cli.resolve_game_id", return_value=resolution):
                result = command_init(arguments)
            config = load_config(config_path)

        self.assertEqual(0, result)
        self.assertEqual("340", config["game_id"])
        self.assertEqual("340", config["game_resolution"]["game_id"])
        self.assertEqual("https://www.youtube.com/watch?v=AbC_123-xY", config["video_url"])

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
