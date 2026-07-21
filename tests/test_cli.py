import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pwhl_shot_tracking.cli import (
    command_discover_anchors,
    command_finalize,
    command_init,
    command_render,
    command_run_game,
    command_sync,
    command_tag,
)
from pwhl_shot_tracking.clock import Anchor
from pwhl_shot_tracking.config import create_config, load_config, work_path
from pwhl_shot_tracking.utils import read_csv, read_json, write_csv, write_json


class CliTests(unittest.TestCase):
    def test_run_game_orchestrates_defaults_without_persisting_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            output_path = Path(directory) / "shot-chart.png"
            create_config(
                config_path,
                "340",
                "https://www.youtube.com/watch?v=AbC_123-xY",
                "shots_on_goal",
            )
            arguments = argparse.Namespace(
                api_key="test-secret-key",
                video_url="https://youtu.be/AbC_123-xY",
                config=str(config_path),
                output=str(output_path),
                shot_universe="shots_on_goal",
                chunk_seconds=300,
                limit=None,
                open_chart=False,
            )

            def tag_side_effect(_):
                self.assertEqual("test-secret-key", os.environ.get("GEMINI_API_KEY"))
                return 0

            with patch.dict(os.environ, {"GEMINI_API_KEY": "previous-key"}), patch(
                "pwhl_shot_tracking.cli.command_fetch", return_value=0
            ) as fetch, patch(
                "pwhl_shot_tracking.cli.command_discover_anchors", return_value=0
            ) as discover, patch(
                "pwhl_shot_tracking.cli.command_sync", return_value=0
            ) as sync, patch(
                "pwhl_shot_tracking.cli.command_tag", side_effect=tag_side_effect
            ) as tag, patch(
                "pwhl_shot_tracking.cli.command_render", return_value=0
            ) as render:
                result = command_run_game(arguments)
                restored_key = os.environ.get("GEMINI_API_KEY")

            config_text = config_path.read_text(encoding="utf-8")

        self.assertEqual(0, result)
        self.assertEqual("previous-key", restored_key)
        self.assertNotIn("test-secret-key", config_text)
        fetch.assert_called_once()
        discover.assert_called_once()
        sync.assert_called_once()
        tag.assert_called_once()
        render.assert_called_once()

    def test_anchor_discovery_uses_duration_detected_during_init(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            create_config(
                config_path,
                "340",
                "https://www.youtube.com/watch?v=AbC_123-xY",
                "shots_on_goal",
                game_resolution={"video": {"duration_seconds": 120}},
            )
            output_path = Path(directory) / "anchors.csv"
            arguments = argparse.Namespace(
                config=str(config_path),
                start=0,
                end=None,
                chunk_seconds=300,
                output=str(output_path),
                yes=True,
            )
            discovered = [Anchor(1, 1190, 10.0, source="gemini")]
            with patch("pwhl_shot_tracking.cli.GeminiClient", return_value=object()), patch(
                "pwhl_shot_tracking.cli.discover_anchors",
                return_value=(discovered, {}),
            ) as discover:
                result = command_discover_anchors(arguments)

            report = read_json(
                Path(directory) / "work" / "340" / "sync" / "discovery_report.json"
            )

        self.assertEqual(0, result)
        self.assertEqual(120.0, report["range"]["end_seconds"])
        self.assertEqual((0.0, 120.0), discover.call_args.args[1:3])

    def test_tag_limit_merges_into_existing_rows_and_needs_no_key_when_cached(self):
        def sidecar(start, end):
            return {
                "status": "complete",
                "offsets": {"start_seconds": start, "end_seconds": end},
                "passes": {
                    "contextual": {"parsed_response": {"royal_road": False, "shooter_number": "9"}},
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

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            create_config(
                config_path,
                "1",
                "https://www.youtube.com/watch?v=test",
                "shots_on_goal",
            )
            config = load_config(config_path)
            write_json(work_path(config, "feed", "metadata.json"), {"feed_sha256": "hash"})
            rows = []
            for index, shot_id in enumerate(["a", "b"]):
                start = 100.0 + index * 50
                rows.append(
                    {
                        "shot_id": shot_id,
                        "shooter_number": "9",
                        "x": 100,
                        "y": 150,
                        "sync_status": "exact",
                        "video_seconds": start + 12,
                        "clip_start_seconds": start,
                        "clip_end_seconds": start + 14,
                    }
                )
                write_json(work_path(config, "clips", "%s.json" % shot_id), sidecar(start, start + 14))
            rows.append({"shot_id": "c", "shooter_number": "9", "sync_status": "unmapped",
                         "video_seconds": "", "clip_start_seconds": "", "clip_end_seconds": ""})
            write_csv(work_path(config, "shots_synced.csv"), rows)

            # Everything is cached, so no Gemini client (and no key) is needed.
            with patch("pwhl_shot_tracking.cli.GeminiClient", side_effect=AssertionError("no client expected")):
                command_tag(argparse.Namespace(config=str(config_path), limit=None, force=False, yes=False))
                tagged_before = read_csv(work_path(config, "royal_road_1.csv"))
                command_tag(argparse.Namespace(config=str(config_path), limit=1, force=False, yes=False))
                tagged_after = read_csv(work_path(config, "royal_road_1.csv"))

        self.assertEqual(["a", "b"], [row["shot_id"] for row in tagged_before])
        # A limited smoke test must not clobber previously tagged rows.
        self.assertEqual(["a", "b"], [row["shot_id"] for row in tagged_after])

    def test_sync_refuses_an_empty_anchor_file(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            create_config(
                config_path,
                "1",
                "https://www.youtube.com/watch?v=test",
                "shots_on_goal",
            )
            config = load_config(config_path)
            write_csv(work_path(config, "shots.csv"), [{"shot_id": "a", "period": 1, "remaining_seconds": 100}])
            (work_path(config, "sync", "anchors.csv")).parent.mkdir(parents=True, exist_ok=True)
            (work_path(config, "sync", "anchors.csv")).write_text(
                "period,game_clock,video_seconds,clock_running\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                command_sync(argparse.Namespace(config=str(config_path), shots=None, anchors=None))

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

    def test_render_creates_watermarked_draft_directly_from_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "game.json"
            create_config(
                config_path,
                "340",
                "https://www.youtube.com/watch?v=AbC_123-xY",
                "shots_on_goal",
            )
            config = load_config(config_path)
            write_csv(
                work_path(config, "royal_road_340.csv"),
                [
                    {
                        "shot_id": "shot-1",
                        "team_code": "MTL",
                        "x": 100,
                        "y": 150,
                        "shooter_number": "29",
                        "tag_royal_road": True,
                    }
                ],
            )
            output_path = Path(directory) / "shot-chart.png"
            result = command_render(
                argparse.Namespace(
                    config=str(config_path),
                    output=str(output_path),
                    publish=False,
                )
            )
            report = read_json(work_path(config, "render_report.json"))
            signature = output_path.read_bytes()[:8]

        self.assertEqual(0, result)
        self.assertEqual(b"\x89PNG\r\n\x1a\n", signature)
        self.assertFalse(report["publishable"])
        self.assertEqual("model_tags", report["source_rows"])

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
