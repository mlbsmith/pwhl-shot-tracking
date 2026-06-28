import binascii
import math
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .utils import as_bool, optional_float


Color = Tuple[int, int, int]

FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    "#": ["01010", "11111", "01010", "01010", "11111", "01010", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ":": ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00100", "00100"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    " ": ["00000"] * 7,
}


def parse_hex(value: str, fallback: Color = (30, 60, 90)) -> Color:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore
    except ValueError:
        return fallback


class Canvas:
    def __init__(self, width: int, height: int, background: Color):
        self.width = width
        self.height = height
        self.pixels = bytearray(bytes(background) * (width * height))

    def pixel(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset : offset + 3] = bytes(color)

    def rect(self, x: int, y: int, width: int, height: int, color: Color, fill: bool = True) -> None:
        if fill:
            x0, x1 = max(0, x), min(self.width, x + width)
            y0, y1 = max(0, y), min(self.height, y + height)
            row = bytes(color) * max(0, x1 - x0)
            for py in range(y0, y1):
                offset = (py * self.width + x0) * 3
                self.pixels[offset : offset + len(row)] = row
        else:
            self.line(x, y, x + width, y, color, 2)
            self.line(x + width, y, x + width, y + height, color, 2)
            self.line(x + width, y + height, x, y + height, color, 2)
            self.line(x, y + height, x, y, color, 2)

    def circle(self, cx: int, cy: int, radius: int, color: Color, fill: bool = True) -> None:
        radius = max(0, radius)
        if fill:
            for dy in range(-radius, radius + 1):
                span = int(math.sqrt(max(0, radius * radius - dy * dy)))
                self.rect(cx - span, cy + dy, span * 2 + 1, 1, color, True)
        else:
            steps = max(24, radius * 6)
            points = [
                (
                    int(round(cx + radius * math.cos(index * 2 * math.pi / steps))),
                    int(round(cy + radius * math.sin(index * 2 * math.pi / steps))),
                )
                for index in range(steps + 1)
            ]
            for start, end in zip(points, points[1:]):
                self.line(start[0], start[1], end[0], end[1], color, 2)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: Color, width: int = 1) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        radius = max(0, width // 2)
        while True:
            if radius:
                self.circle(x0, y0, radius, color, True)
            else:
                self.pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def arrow(self, x0: int, y0: int, x1: int, y1: int, color: Color, width: int = 4) -> None:
        self.line(x0, y0, x1, y1, color, width)
        angle = math.atan2(y1 - y0, x1 - x0)
        length = 18
        for delta in (2.55, -2.55):
            hx = int(round(x1 + length * math.cos(angle + delta)))
            hy = int(round(y1 + length * math.sin(angle + delta)))
            self.line(x1, y1, hx, hy, color, width)

    def text(self, x: int, y: int, text: str, color: Color, scale: int = 3) -> None:
        cursor = x
        for char in str(text).upper():
            glyph = FONT.get(char, FONT[" "])
            for row_index, row in enumerate(glyph):
                for column_index, bit in enumerate(row):
                    if bit == "1":
                        self.rect(
                            cursor + column_index * scale,
                            y + row_index * scale,
                            scale,
                            scale,
                            color,
                            True,
                        )
            cursor += 6 * scale

    def save_png(self, path: Path) -> None:
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)
            start = y * stride
            raw.extend(self.pixels[start : start + stride])

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

        payload = b"\x89PNG\r\n\x1a\n"
        payload += chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
        payload += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        payload += chunk(b"IEND", b"")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _feed_to_canvas(x: float, y: float, rink: Tuple[int, int, int, int]) -> Tuple[int, int]:
    left, top, width, height = rink
    return int(round(left + x / 600.0 * width)), int(round(top + y / 300.0 * height))


def _pass_origin(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    confidence = str(row.get("tag_pass_geometry_confidence") or "")
    longitudinal = optional_float(row.get("tag_pass_origin_longitudinal_pct"))
    lateral = optional_float(row.get("tag_pass_origin_lateral_pct"))
    shot_x = optional_float(row.get("x"))
    if confidence not in {"high", "medium"} or longitudinal is None or lateral is None or shot_x is None:
        return None
    attacks_left = shot_x < 300
    if attacks_left:
        x = 60.0 + longitudinal / 100.0 * 165.0
        y = 300.0 - lateral / 100.0 * 300.0
    else:
        x = 540.0 - longitudinal / 100.0 * 165.0
        y = lateral / 100.0 * 300.0
    return x, y


def render_shot_map(
    rows: Iterable[Dict[str, Any]],
    config: Dict[str, Any],
    output_path: Path,
    publishable: bool,
) -> Dict[str, Any]:
    materialized = list(rows)
    canvas = Canvas(1800, 1050, (242, 246, 249))
    rink = (90, 190, 1620, 810)
    left, top, width, height = rink
    canvas.rect(left, top, width, height, (250, 253, 255), True)
    canvas.rect(left, top, width, height, (69, 93, 112), False)

    # Standard rink reference lines in the feed's 600 x 300 coordinate space.
    for feed_x, color, thickness in [
        (60, (190, 45, 55), 4),
        (225, (34, 94, 168), 5),
        (300, (190, 45, 55), 4),
        (375, (34, 94, 168), 5),
        (540, (190, 45, 55), 4),
    ]:
        px, _ = _feed_to_canvas(feed_x, 0, rink)
        canvas.line(px, top, px, top + height, color, thickness)
    center_y = _feed_to_canvas(0, 150, rink)[1]
    for x in range(left, left + width, 28):
        canvas.line(x, center_y, min(x + 14, left + width), center_y, (160, 172, 181), 2)
    for feed_x in (100, 203, 397, 500):
        for feed_y in (75, 225):
            cx, cy = _feed_to_canvas(feed_x, feed_y, rink)
            canvas.circle(cx, cy, 82, (210, 65, 75), False)
    canvas.circle(left + width // 2, top + height // 2, 82, (34, 94, 168), False)

    teams = []
    for row in materialized:
        code = str(row.get("team_code") or row.get("team_id") or "TEAM")
        if code not in teams:
            teams.append(code)
    colors: Dict[str, Color] = {}
    defaults = [
        parse_hex(config.get("team_colors", {}).get("DEFAULT_HOME"), (109, 32, 119)),
        parse_hex(config.get("team_colors", {}).get("DEFAULT_AWAY"), (27, 54, 93)),
    ]
    for index, team in enumerate(teams):
        colors[team] = parse_hex(config.get("team_colors", {}).get(team), defaults[index % len(defaults)])

    royal_rows = []
    for row in materialized:
        x = optional_float(row.get("x"))
        y = optional_float(row.get("y"))
        if x is None or y is None:
            continue
        px, py = _feed_to_canvas(x, y, rink)
        canvas.circle(px, py, 6, (166, 177, 186), True)
        final_value = row.get("final_royal_road")
        royal = as_bool(final_value) if final_value not in ("", None) else as_bool(row.get("tag_royal_road"))
        if royal:
            royal_rows.append(row)

    for row in royal_rows:
        x = float(row["x"])
        y = float(row["y"])
        px, py = _feed_to_canvas(x, y, rink)
        team = str(row.get("team_code") or row.get("team_id") or "TEAM")
        color = colors[team]
        origin = _pass_origin(row)
        if origin is not None:
            ox, oy = _feed_to_canvas(origin[0], origin[1], rink)
            canvas.arrow(ox, oy, px, py, color, 5)
        canvas.circle(px, py, 14, (255, 255, 255), True)
        canvas.circle(px, py, 11, color, True)
        number = str(row.get("shooter_number") or "")
        if number:
            canvas.text(px + 17, py - 10, "#" + number, (24, 35, 43), 2)

    counts = {team: 0 for team in teams}
    for row in royal_rows:
        team = str(row.get("team_code") or row.get("team_id") or "TEAM")
        counts[team] = counts.get(team, 0) + 1
    headline = "ROYAL ROAD CHANCES"
    canvas.text(90, 45, headline, (24, 35, 43), 6)
    summary = "   ".join("%s %d" % (team, counts.get(team, 0)) for team in teams)
    canvas.text(92, 115, summary or "NO TEAMS", (56, 72, 84), 4)
    denominator = str(config.get("shot_universe", "shots_on_goal")).replace("_", " ")
    canvas.text(1040, 125, "DENOMINATOR: " + denominator, (74, 91, 102), 2)

    if not publishable:
        canvas.rect(0, 0, 1800, 32, (174, 43, 55), True)
        canvas.text(530, 6, "DRAFT - MANUAL REVIEW OR THRESHOLDS INCOMPLETE", (255, 255, 255), 3)
    canvas.text(95, 1010, "ALL SHOTS FAINT. ROYAL ROAD CHANCES HIGHLIGHTED. ARROWS ONLY WHEN GEOMETRY IS MEDIUM OR HIGH.", (65, 80, 91), 2)
    canvas.save_png(output_path)
    return {
        "output_path": str(output_path),
        "width": canvas.width,
        "height": canvas.height,
        "shot_count": len(materialized),
        "royal_road_count": len(royal_rows),
        "team_counts": counts,
        "publishable": publishable,
    }
