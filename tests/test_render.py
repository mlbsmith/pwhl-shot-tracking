import struct
import tempfile
import unittest
from pathlib import Path

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.render import (
    LABEL_HEIGHT,
    Canvas,
    _draw_rink,
    _feed_to_canvas,
    _place_label,
    _team_colors,
    render_shot_map,
)


class RenderTests(unittest.TestCase):
    def test_rink_uses_regulation_background_features(self):
        page = (242, 246, 249)
        ice = (250, 253, 255)
        red = (204, 45, 55)
        blue = (34, 94, 168)
        canvas = Canvas(1800, 1050, page)
        rink = (90, 220, 1620, 689)
        _draw_rink(canvas, rink)

        def color_at(feed_x, feed_y):
            x, y = _feed_to_canvas(feed_x, feed_y, rink)
            offset = (y * canvas.width + x) * 3
            return tuple(canvas.pixels[offset : offset + 3])

        # Rounded board corners leave the square bounding-box corner outside ice.
        corner_offset = (rink[1] * canvas.width + rink[0]) * 3
        self.assertEqual(page, tuple(canvas.pixels[corner_offset : corner_offset + 3]))
        # Four end-zone circles, neutral dots, and the centre spot are present.
        self.assertEqual(red, color_at(138, 72))
        self.assertEqual(red, color_at(240, 72))
        self.assertEqual(blue, color_at(300, 150))
        # The former extra neutral-zone faceoff circle is now clean ice.
        self.assertEqual(ice, color_at(203, 30))

    def test_labels_flip_inside_the_rink_and_avoid_each_other(self):
        rink = (90, 220, 1620, 689)
        left, top, width, height = rink
        text_width = 36
        obstacles = []
        # A marker against the right boards cannot fit a right-side label.
        x, y = _place_label(left + width - 20, top + 300, text_width, rink, obstacles)
        self.assertLessEqual(x + text_width, left + width - 6)
        # A second marker at the same spot must not overlap the first label.
        x2, y2 = _place_label(left + width - 20, top + 300, text_width, rink, obstacles)
        first = (x, y, x + text_width, y + LABEL_HEIGHT)
        second = (x2, y2, x2 + text_width, y2 + LABEL_HEIGHT)
        overlap = not (
            first[2] <= second[0]
            or second[2] <= first[0]
            or first[3] <= second[1]
            or second[3] <= first[1]
        )
        self.assertFalse(overlap)

    def test_team_colors_follow_home_and_away_designations(self):
        config = {
            "team_colors": {
                "DEFAULT_HOME": "#6D2077",
                "DEFAULT_AWAY": "#1B365D",
            }
        }
        # The away team appears first in row order; it must still get the
        # away default rather than inheriting home colors by encounter order.
        rows = [
            {"team_code": "MIN", "is_home": "False"},
            {"team_code": "MTL", "is_home": "True"},
        ]
        colors = _team_colors(rows, config, ["MIN", "MTL"])
        self.assertEqual((27, 54, 93), colors["MIN"])
        self.assertEqual((109, 32, 119), colors["MTL"])
        # Without home/away information the encounter-order fallback applies.
        colors = _team_colors([{"team_code": "A"}, {"team_code": "B"}], config, ["A", "B"])
        self.assertNotEqual(colors["A"], colors["B"])

    def test_arrows_require_geometry_that_crosses_the_royal_road(self):
        def royal_row(shot_id, y, lateral_pct):
            return {
                "shot_id": shot_id,
                "team_code": "MTL",
                "x": 100,
                "y": y,
                "shooter_number": "29",
                "final_royal_road": True,
                "tag_pass_geometry_confidence": "high",
                "tag_pass_origin_longitudinal_pct": 80,
                "tag_pass_origin_lateral_pct": lateral_pct,
            }

        config = dict(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            report = render_shot_map(
                [
                    # Shot below centre (y=200), origin on the far side: drawn.
                    royal_row("crossing", 200, 90),
                    # Shot below centre, origin also below centre: suppressed.
                    royal_row("same-side", 200, 10),
                ],
                config,
                Path(directory) / "map.png",
                publishable=True,
            )
        self.assertEqual(2, report["royal_road_count"])
        self.assertEqual(1, report["arrow_count"])

    def test_draft_render_carries_the_watermark_banner(self):
        config = dict(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.png"
            render_shot_map([], config, path, publishable=False)
            draft_payload = path.read_bytes()
            render_shot_map([], config, path, publishable=True)
            publish_payload = path.read_bytes()
        self.assertNotEqual(draft_payload, publish_payload)

    def test_writes_high_resolution_png_without_dependencies(self):
        rows = [
            {
                "shot_id": "shot-1",
                "team_code": "MTL",
                "x": 100,
                "y": 130,
                "shooter_number": "29",
                "final_royal_road": True,
                "tag_pass_geometry_confidence": "high",
                "tag_pass_origin_longitudinal_pct": 80,
                "tag_pass_origin_lateral_pct": 10,
            },
            {
                "shot_id": "shot-2",
                "team_code": "OTT",
                "x": 500,
                "y": 180,
                "shooter_number": "19",
                "final_royal_road": False,
            },
        ]
        config = dict(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.png"
            report = render_shot_map(rows, config, path, publishable=True)
            payload = path.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", payload[:8])
        width, height = struct.unpack(">II", payload[16:24])
        self.assertEqual((1800, 1050), (width, height))
        self.assertEqual(1, report["royal_road_count"])


if __name__ == "__main__":
    unittest.main()
