import struct
import tempfile
import unittest
from pathlib import Path

from pwhl_shot_tracking.config import DEFAULT_CONFIG
from pwhl_shot_tracking.render import render_shot_map


class RenderTests(unittest.TestCase):
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
