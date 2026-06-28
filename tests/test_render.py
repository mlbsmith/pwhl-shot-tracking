import struct
import tempfile
import unittest
from pathlib import Path

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.render import Canvas, _draw_rink, _feed_to_canvas, render_shot_map


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
