import json
import unittest

from pwhl_shot_tracking.resolver import (
    dates_in_title,
    extract_youtube_video_id,
    fetch_youtube_metadata,
    select_game,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


def game(game_id, played, home="Montréal Victoire", away="Minnesota Frost"):
    return {
        "game_id": str(game_id),
        "season_id": "9",
        "date_played": played,
        "home_team_name": home,
        "home_team_city": home.split()[0],
        "home_team_nickname": home.split()[-1],
        "home_team_code": "MTL",
        "visiting_team_name": away,
        "visiting_team_city": away.split()[0],
        "visiting_team_nickname": away.split()[-1],
        "visiting_team_code": "MIN",
    }


class ResolverTests(unittest.TestCase):
    def test_extracts_common_youtube_url_forms(self):
        expected = "AbC_123-xY"
        self.assertEqual(expected, extract_youtube_video_id("https://youtu.be/" + expected))
        self.assertEqual(expected, extract_youtube_video_id("https://www.youtube.com/watch?v=" + expected))
        self.assertEqual(expected, extract_youtube_video_id("https://www.youtube.com/live/" + expected))

    def test_parses_dates_embedded_in_titles(self):
        self.assertEqual(
            ["2026-05-02"],
            [value.isoformat() for value in dates_in_title("MTL vs MIN | May 2, 2026")],
        )
        self.assertEqual(
            ["2026-05-02"],
            [value.isoformat() for value in dates_in_title("PWHL 2026-05-02")],
        )

    def test_selects_clear_team_and_date_match(self):
        metadata = {
            "title": "Montreal Victoire vs Minnesota Frost | May 2, 2026",
            "title_dates": ["2026-05-02"],
            "publish_date": "2026-05-02",
        }
        report = select_game(
            metadata,
            [
                game("340", "2026-05-02"),
                game("341", "2026-05-05"),
                game("338", "2026-05-02", home="Boston Fleet", away="Ottawa Charge"),
            ],
        )
        self.assertEqual("resolved", report["status"])
        self.assertEqual("340", report["game_id"])
        self.assertGreaterEqual(report["top_score_margin"], 10)

    def test_refuses_indistinguishable_schedule_rows(self):
        metadata = {
            "title": "Montreal vs Minnesota",
            "title_dates": [],
            "publish_date": "2026-05-02",
        }
        report = select_game(metadata, [game("340", "2026-05-02"), game("999", "2026-05-02")])
        self.assertEqual("ambiguous", report["status"])
        self.assertIsNone(report["game_id"])

    def test_fetches_public_metadata_without_youtube_data_api_key(self):
        def opener(request, timeout):
            if "oembed" in request.full_url:
                return FakeResponse(
                    json.dumps(
                        {
                            "title": "Montreal vs Minnesota | May 2, 2026",
                            "author_name": "The PWHL",
                        }
                    ).encode("utf-8")
                )
            return FakeResponse(
                b'{"publishDate":"2026-05-02T12:00:00-04:00",'
                b'"uploadDate":"2026-05-02T12:00:00-04:00",'
                b'"lengthSeconds":"7200","ownerChannelName":"The PWHL"}'
            )

        metadata = fetch_youtube_metadata(
            "https://www.youtube.com/watch?v=AbC_123-xY",
            opener=opener,
        )
        self.assertEqual("2026-05-02", metadata["publish_date"])
        self.assertEqual(7200, metadata["duration_seconds"])
        self.assertEqual(["2026-05-02"], metadata["title_dates"])


if __name__ == "__main__":
    unittest.main()
