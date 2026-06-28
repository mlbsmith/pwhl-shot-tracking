import unittest

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.feed import normalize_shots


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "GC": {
                "Pxpverbose": [
                    {
                        "event": "shot",
                        "id": "10",
                        "period_id": "1",
                        "s": 100,
                        "home": "1",
                        "team_id": "3",
                        "x_location": 110,
                        "y_location": 77,
                        "game_goal_id": "",
                        "player": {
                            "player_id": "31",
                            "jersey_number": "29",
                            "team_id": "3",
                            "team_code": "MTL",
                            "first_name": "Marie-Philip",
                            "last_name": "Poulin",
                        },
                    },
                    {
                        "event": "missed_shot",
                        "id": "11",
                        "period_id": "1",
                        "s": 110,
                        "team_id": "5",
                        "x_location": 500,
                        "y_location": 100,
                        "player": {"player_id": "50", "jersey_number": "21", "team_code": "OTT"},
                    },
                    {
                        "event": "blocked_shot",
                        "id": "12",
                        "period_id": "1",
                        "seconds": 120,
                        "team_id": "5",
                        "x_location": 490,
                        "y_location": 130,
                        "player": {"player_id": "51", "jersey_number": "22", "team_code": "OTT"},
                    },
                ]
            }
        }

    def config(self, universe):
        config = dict(DEFAULT_CONFIG)
        config["game_id"] = "137"
        config["shot_universe"] = universe
        return config

    def test_shots_on_goal_normalizes_elapsed_to_remaining(self):
        shots, report = normalize_shots(self.payload, self.config("shots_on_goal"))
        self.assertEqual(1, len(shots))
        self.assertEqual("18:20", shots[0]["time_remaining"])
        self.assertEqual("MTL", shots[0]["team_code"])
        self.assertEqual(1.0, report["coordinate_coverage"])

    def test_denominators_are_explicit(self):
        fenwick, _ = normalize_shots(self.payload, self.config("fenwick"))
        attempts, _ = normalize_shots(self.payload, self.config("all_attempts"))
        self.assertEqual(["shot", "missed_shot"], [row["event_type"] for row in fenwick])
        self.assertEqual(3, len(attempts))


if __name__ == "__main__":
    unittest.main()
