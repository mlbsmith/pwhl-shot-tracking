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

    def ellipse(
        self,
        cx: int,
        cy: int,
        radius_x: int,
        radius_y: int,
        color: Color,
        fill: bool = True,
    ) -> None:
        radius_x = max(1, radius_x)
        radius_y = max(1, radius_y)
        if fill:
            for dy in range(-radius_y, radius_y + 1):
                ratio = 1.0 - (dy * dy) / float(radius_y * radius_y)
                span = int(round(radius_x * math.sqrt(max(0.0, ratio))))
                self.rect(cx - span, cy + dy, span * 2 + 1, 1, color, True)
        else:
            self.ellipse_arc(cx, cy, radius_x, radius_y, 0.0, 2 * math.pi, color, 2)

    def ellipse_arc(
        self,
        cx: int,
        cy: int,
        radius_x: int,
        radius_y: int,
        start_angle: float,
        end_angle: float,
        color: Color,
        width: int = 2,
    ) -> None:
        sweep = end_angle - start_angle
        steps = max(12, int(max(radius_x, radius_y) * abs(sweep) / 3))
        points = [
            (
                int(round(cx + radius_x * math.cos(start_angle + sweep * index / steps))),
                int(round(cy + radius_y * math.sin(start_angle + sweep * index / steps))),
            )
            for index in range(steps + 1)
        ]
        for start, end in zip(points, points[1:]):
            self.line(start[0], start[1], end[0], end[1], color, width)

    def rounded_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        radius: int,
        color: Color,
        fill: bool = True,
        stroke_width: int = 2,
    ) -> None:
        radius = max(1, min(radius, width // 2, height // 2))
        if fill:
            self.rect(x + radius, y, width - 2 * radius, height, color, True)
            self.rect(x, y + radius, width, height - 2 * radius, color, True)
            for cx, cy in (
                (x + radius, y + radius),
                (x + width - radius, y + radius),
                (x + radius, y + height - radius),
                (x + width - radius, y + height - radius),
            ):
                self.circle(cx, cy, radius, color, True)
            return
        self.line(x + radius, y, x + width - radius, y, color, stroke_width)
        self.line(x + width, y + radius, x + width, y + height - radius, color, stroke_width)
        self.line(x + width - radius, y + height, x + radius, y + height, color, stroke_width)
        self.line(x, y + height - radius, x, y + radius, color, stroke_width)
        self.ellipse_arc(
            x + radius,
            y + radius,
            radius,
            radius,
            math.pi,
            1.5 * math.pi,
            color,
            stroke_width,
        )
        self.ellipse_arc(
            x + width - radius,
            y + radius,
            radius,
            radius,
            1.5 * math.pi,
            2 * math.pi,
            color,
            stroke_width,
        )
        self.ellipse_arc(
            x + width - radius,
            y + height - radius,
            radius,
            radius,
            0.0,
            0.5 * math.pi,
            color,
            stroke_width,
        )
        self.ellipse_arc(
            x + radius,
            y + height - radius,
            radius,
            radius,
            0.5 * math.pi,
            math.pi,
            color,
            stroke_width,
        )

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


def _clamp_to_rink(
    x: int,
    y: int,
    rink: Tuple[int, int, int, int],
    margin: int = 18,
) -> Tuple[int, int]:
    left, top, width, height = rink
    radius = int(round(width * 84 / 600.0))
    x = min(max(x, left + margin), left + width - margin)
    y = min(max(y, top + margin), top + height - margin)
    inner_radius = max(1, radius - margin)
    corner_x = left + radius if x < left + radius else left + width - radius
    corner_y = top + radius if y < top + radius else top + height - radius
    in_corner_x = x < left + radius or x > left + width - radius
    in_corner_y = y < top + radius or y > top + height - radius
    if in_corner_x and in_corner_y:
        dx = x - corner_x
        dy = y - corner_y
        distance = math.hypot(dx, dy)
        if distance > inner_radius:
            x = int(round(corner_x + dx / distance * inner_radius))
            y = int(round(corner_y + dy / distance * inner_radius))
    return x, y


def _draw_rink(canvas: Canvas, rink: Tuple[int, int, int, int]) -> None:
    left, top, width, height = rink
    ice = (250, 253, 255)
    boards = (42, 69, 86)
    red = (204, 45, 55)
    blue = (34, 94, 168)
    crease = (211, 235, 248)
    # The feed grid converts to a 200 x 85 foot rink:
    #   x_feet = (x - 300) / 3
    #   y_feet = (y - 150) * 85 / 300
    # Keeping that aspect ratio makes regulation circles circular on the PNG.
    corner_radius = int(round(width * 84 / 600.0))  # 28-foot board radius

    canvas.rounded_rect(left, top, width, height, corner_radius, ice, True)

    # Creases sit in front of the goal lines; erase the back half so they are
    # genuine semicircles rather than full ovals.
    left_goal = _feed_to_canvas(33, 150, rink)
    right_goal = _feed_to_canvas(567, 150, rink)
    crease_rx = int(round(width * 18 / 600.0))
    crease_ry = int(round(height * 22 / 300.0))
    for goal_x, goal_y, direction in (
        (left_goal[0], left_goal[1], 1),
        (right_goal[0], right_goal[1], -1),
    ):
        canvas.ellipse(goal_x, goal_y, crease_rx, crease_ry, crease, True)
        if direction > 0:
            canvas.rect(goal_x - crease_rx, goal_y - crease_ry, crease_rx, crease_ry * 2 + 1, ice, True)
            canvas.ellipse_arc(
                goal_x,
                goal_y,
                crease_rx,
                crease_ry,
                -0.5 * math.pi,
                0.5 * math.pi,
                red,
                2,
            )
        else:
            canvas.rect(goal_x + 1, goal_y - crease_ry, crease_rx, crease_ry * 2 + 1, ice, True)
            canvas.ellipse_arc(
                goal_x,
                goal_y,
                crease_rx,
                crease_ry,
                0.5 * math.pi,
                1.5 * math.pi,
                red,
                2,
            )

    # Goal, blue, and centre lines in the HockeyTech 600 x 300 coordinate frame.
    def vertical_bounds(px: int) -> Tuple[int, int]:
        if px < left + corner_radius:
            dx = left + corner_radius - px
        elif px > left + width - corner_radius:
            dx = px - (left + width - corner_radius)
        else:
            return top, top + height
        inset = corner_radius - math.sqrt(max(0.0, corner_radius * corner_radius - dx * dx))
        return int(round(top + inset)), int(round(top + height - inset))

    for feed_x, color, thickness in (
        (33, red, 3),
        (225, blue, 6),
        (300, red, 4),
        (375, blue, 6),
        (567, red, 3),
    ):
        px, _ = _feed_to_canvas(feed_x, 0, rink)
        line_top, line_bottom = vertical_bounds(px)
        canvas.line(px, line_top + 3, px, line_bottom - 3, color, thickness)

    faceoff_radius_x = int(round(width * 45 / 600.0))  # 15 feet
    faceoff_radius_y = int(round(height * 53 / 300.0))
    faceoff_centres = [(93, 72), (93, 228), (507, 72), (507, 228)]
    for feed_x, feed_y in faceoff_centres:
        cx, cy = _feed_to_canvas(feed_x, feed_y, rink)
        canvas.ellipse(cx, cy, faceoff_radius_x, faceoff_radius_y, red, False)
        canvas.circle(cx, cy, 7, red, True)
        tick = 13
        canvas.line(
            cx - faceoff_radius_x - tick,
            cy,
            cx - faceoff_radius_x + tick,
            cy,
            red,
            2,
        )
        canvas.line(
            cx + faceoff_radius_x - tick,
            cy,
            cx + faceoff_radius_x + tick,
            cy,
            red,
            2,
        )

    for feed_x, feed_y in ((240, 72), (240, 228), (360, 72), (360, 228)):
        cx, cy = _feed_to_canvas(feed_x, feed_y, rink)
        canvas.circle(cx, cy, 7, red, True)

    centre_x, centre_y = _feed_to_canvas(300, 150, rink)
    canvas.ellipse(
        centre_x,
        centre_y,
        faceoff_radius_x,
        faceoff_radius_y,
        blue,
        False,
    )
    canvas.circle(centre_x, centre_y, 8, blue, True)

    # Nets are behind each goal line and use rounded backs.
    net_rx = int(round(width * 18 / 600.0))
    net_ry = int(round(height * 11 / 300.0))
    canvas.ellipse_arc(
        left_goal[0],
        left_goal[1],
        net_rx,
        net_ry,
        0.5 * math.pi,
        1.5 * math.pi,
        red,
        3,
    )
    canvas.line(
        left_goal[0],
        left_goal[1] - net_ry,
        left_goal[0],
        left_goal[1] + net_ry,
        red,
        3,
    )
    canvas.ellipse_arc(
        right_goal[0],
        right_goal[1],
        net_rx,
        net_ry,
        -0.5 * math.pi,
        0.5 * math.pi,
        red,
        3,
    )
    canvas.line(
        right_goal[0],
        right_goal[1] - net_ry,
        right_goal[0],
        right_goal[1] + net_ry,
        red,
        3,
    )

    canvas.rounded_rect(left, top, width, height, corner_radius, boards, False, 4)


def _pass_origin(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    confidence = str(row.get("tag_pass_geometry_confidence") or "")
    longitudinal = optional_float(row.get("tag_pass_origin_longitudinal_pct"))
    lateral = optional_float(row.get("tag_pass_origin_lateral_pct"))
    shot_x = optional_float(row.get("x"))
    if confidence not in {"high", "medium"} or longitudinal is None or lateral is None or shot_x is None:
        return None
    attacks_left = shot_x < 300
    if attacks_left:
        x = 33.0 + longitudinal / 100.0 * 192.0
        y = 300.0 - lateral / 100.0 * 300.0
    else:
        x = 567.0 - longitudinal / 100.0 * 192.0
        y = lateral / 100.0 * 300.0
    return x, y


LABEL_SCALE = 2
LABEL_HEIGHT = 7 * LABEL_SCALE
MARKER_RADIUS = 14


def _boxes_overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int], pad: int = 2) -> bool:
    return not (
        a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1]
    )


def _place_label(
    px: int,
    py: int,
    text_width: int,
    rink: Tuple[int, int, int, int],
    obstacles: List[Tuple[int, int, int, int]],
) -> Tuple[int, int]:
    """Pick a label position beside the marker that stays on the ice and clear
    of already-placed labels and markers. Falls back to the classic right-side
    spot when every candidate collides."""
    left, top, width, height = rink
    gap = MARKER_RADIUS + 3
    candidates = [
        (px + gap, py - LABEL_HEIGHT // 2),
        (px - gap - text_width, py - LABEL_HEIGHT // 2),
        (px - text_width // 2, py - gap - LABEL_HEIGHT),
        (px - text_width // 2, py + gap),
    ]
    for step in range(1, 9):
        offset = step * (LABEL_HEIGHT + 6)
        candidates.append((px + gap, py - LABEL_HEIGHT // 2 + offset))
        candidates.append((px - gap - text_width, py - LABEL_HEIGHT // 2 + offset))
        candidates.append((px + gap, py - LABEL_HEIGHT // 2 - offset))
        candidates.append((px - gap - text_width, py - LABEL_HEIGHT // 2 - offset))

    def in_bounds(x: int, y: int) -> bool:
        return (
            left + 6 <= x
            and x + text_width <= left + width - 6
            and top + 6 <= y
            and y + LABEL_HEIGHT <= top + height - 6
        )

    for x, y in candidates:
        if not in_bounds(x, y):
            continue
        box = (x, y, x + text_width, y + LABEL_HEIGHT)
        if any(_boxes_overlap(box, other) for other in obstacles):
            continue
        obstacles.append(box)
        return x, y
    # Every candidate collides: accept an overlap, but never leave the ice.
    x, y = next(((x, y) for x, y in candidates if in_bounds(x, y)), candidates[0])
    obstacles.append((x, y, x + text_width, y + LABEL_HEIGHT))
    return x, y


def _team_colors(materialized: List[Dict[str, Any]], config: Dict[str, Any], teams: List[str]) -> Dict[str, Color]:
    palette = config.get("team_colors", {})
    defaults = [
        parse_hex(palette.get("DEFAULT_HOME"), (109, 32, 119)),
        parse_hex(palette.get("DEFAULT_AWAY"), (27, 54, 93)),
    ]
    team_is_home: Dict[str, bool] = {}
    has_home_info = False
    for row in materialized:
        code = str(row.get("team_code") or row.get("team_id") or "TEAM")
        value = str(row.get("is_home") or "").strip()
        if value:
            has_home_info = True
        if code not in team_is_home:
            team_is_home[code] = as_bool(value)
    # Home/away mode is only trustworthy when it actually distinguishes the
    # teams; degenerate data (both home, or one side missing the field) falls
    # back to encounter order so two teams never share a default color.
    if has_home_info and len({team_is_home.get(team) for team in teams}) < min(2, len(teams)):
        has_home_info = False
    colors: Dict[str, Color] = {}
    for index, team in enumerate(teams):
        if has_home_info:
            default = defaults[0] if team_is_home.get(team) else defaults[1]
        else:
            default = defaults[index % len(defaults)]
        colors[team] = parse_hex(palette.get(team), default)
    return colors


def render_shot_map(
    rows: Iterable[Dict[str, Any]],
    config: Dict[str, Any],
    output_path: Path,
    publishable: bool,
) -> Dict[str, Any]:
    materialized = list(rows)
    canvas = Canvas(1800, 1050, (242, 246, 249))
    rink = (90, 220, 1620, 689)
    _draw_rink(canvas, rink)

    teams = []
    for row in materialized:
        code = str(row.get("team_code") or row.get("team_id") or "TEAM")
        if code not in teams:
            teams.append(code)
    colors = _team_colors(materialized, config, teams)

    royal_rows = []
    for row in materialized:
        x = optional_float(row.get("x"))
        y = optional_float(row.get("y"))
        if x is None or y is None:
            continue
        px, py = _clamp_to_rink(*_feed_to_canvas(x, y, rink), rink)
        canvas.circle(px, py, 6, (166, 177, 186), True)
        final_value = row.get("final_royal_road")
        royal = as_bool(final_value) if final_value not in ("", None) else as_bool(row.get("tag_royal_road"))
        if royal:
            royal_rows.append((row, px, py))

    # Arrows first, then markers, then labels: later strokes never cover a
    # label, and labels can dodge every marker on the ice.
    arrow_count = 0
    for row, px, py in royal_rows:
        team = str(row.get("team_code") or row.get("team_id") or "TEAM")
        origin = _pass_origin(row)
        if origin is None:
            continue
        # A royal-road pass crosses the net-to-net centre line by definition;
        # an arrow whose drawn geometry stays on one side would assert a
        # crossing that is not there, so it is suppressed as unreliable.
        shot_y = float(row["y"])
        if (origin[1] - 150.0) * (shot_y - 150.0) > 0:
            continue
        ox, oy = _clamp_to_rink(
            *_feed_to_canvas(origin[0], origin[1], rink),
            rink,
        )
        canvas.arrow(ox, oy, px, py, colors[team], 5)
        arrow_count += 1

    obstacles: List[Tuple[int, int, int, int]] = []
    for row, px, py in royal_rows:
        team = str(row.get("team_code") or row.get("team_id") or "TEAM")
        canvas.circle(px, py, MARKER_RADIUS, (255, 255, 255), True)
        canvas.circle(px, py, 11, colors[team], True)
        obstacles.append((px - MARKER_RADIUS, py - MARKER_RADIUS, px + MARKER_RADIUS, py + MARKER_RADIUS))

    for row, px, py in royal_rows:
        number = str(row.get("shooter_number") or "")
        if not number:
            continue
        label = "#" + number
        text_width = len(label) * 6 * LABEL_SCALE
        lx, ly = _place_label(px, py, text_width, rink, obstacles)
        canvas.text(lx, ly, label, (24, 35, 43), LABEL_SCALE)

    counts = {team: 0 for team in teams}
    for row, _, _ in royal_rows:
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
    canvas.text(95, 1010, "ALL SHOTS FAINT. ROYAL ROAD CHANCES HIGHLIGHTED. ARROWS ONLY FOR CONFIDENT GEOMETRY THAT CROSSES THE ROYAL ROAD.", (65, 80, 91), 2)
    canvas.save_png(output_path)
    return {
        "output_path": str(output_path),
        "width": canvas.width,
        "height": canvas.height,
        "shot_count": len(materialized),
        "royal_road_count": len(royal_rows),
        "arrow_count": arrow_count,
        "team_counts": counts,
        "publishable": publishable,
    }
