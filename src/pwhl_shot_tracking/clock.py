from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .utils import as_bool, format_clock, parse_clock, read_csv, youtube_timestamp_url


@dataclass(frozen=True)
class Anchor:
    period: int
    remaining_seconds: int
    video_seconds: float
    clock_running: bool = True
    confidence: str = "manual"
    run_id: str = ""
    source: str = "manual"


def load_anchors(path: Path, max_stoppage_gap_seconds: float = 3.0) -> List[Anchor]:
    rows = read_csv(path)
    anchors = []
    for row in rows:
        game_clock = row.get("game_clock") or row.get("time_remaining") or row.get("remaining_seconds")
        if not game_clock:
            raise ValueError("anchor CSV requires game_clock (time remaining)")
        anchors.append(
            Anchor(
                period=int(row["period"]),
                remaining_seconds=parse_clock(game_clock),
                video_seconds=float(row["video_seconds"]),
                clock_running=as_bool(row.get("clock_running", "true")),
                confidence=str(row.get("confidence") or "manual"),
                run_id=str(row.get("run_id") or ""),
                source=str(row.get("source") or "manual"),
            )
        )
    return assign_active_runs(anchors, max_stoppage_gap_seconds)


def assign_active_runs(anchors: Iterable[Anchor], max_stoppage_gap_seconds: float = 3.0) -> List[Anchor]:
    ordered = sorted((anchor for anchor in anchors if anchor.clock_running), key=lambda item: item.video_seconds)
    result = []
    run_number = 0
    previous: Optional[Anchor] = None
    previous_run = ""
    for anchor in ordered:
        explicit = anchor.run_id.strip()
        starts_new = previous is None
        if previous is not None:
            video_delta = anchor.video_seconds - previous.video_seconds
            clock_delta = previous.remaining_seconds - anchor.remaining_seconds
            starts_new = (
                anchor.period != previous.period
                or video_delta <= 0
                or clock_delta <= 0
                or (video_delta - clock_delta) > max_stoppage_gap_seconds
            )
        if explicit:
            run_id = explicit
        elif starts_new or not previous_run:
            run_number += 1
            run_id = "p%d-run-%03d" % (anchor.period, run_number)
        else:
            run_id = previous_run
        result.append(replace(anchor, run_id=run_id))
        previous = anchor
        previous_run = run_id
    return result


def _candidate_segments(anchors: List[Anchor], period: int) -> Iterable[Tuple[Anchor, Anchor]]:
    period_anchors = [anchor for anchor in anchors if anchor.period == period]
    for left, right in zip(period_anchors, period_anchors[1:]):
        if left.run_id == right.run_id:
            yield left, right


def map_clock_to_video(
    anchors: List[Anchor],
    period: int,
    remaining_seconds: int,
    max_extrapolation_seconds: float = 2.0,
) -> Tuple[Optional[float], str, str]:
    exact = [anchor for anchor in anchors if anchor.period == period and anchor.remaining_seconds == remaining_seconds]
    if exact:
        best = min(exact, key=lambda item: item.video_seconds)
        return best.video_seconds, "exact", best.run_id

    for left, right in _candidate_segments(anchors, period):
        high = left.remaining_seconds
        low = right.remaining_seconds
        if high >= remaining_seconds >= low and high != low:
            ratio = (high - remaining_seconds) / float(high - low)
            video = left.video_seconds + ratio * (right.video_seconds - left.video_seconds)
            return video, "interpolated", left.run_id

    period_anchors = [anchor for anchor in anchors if anchor.period == period]
    if period_anchors:
        closest = min(period_anchors, key=lambda item: abs(item.remaining_seconds - remaining_seconds))
        clock_distance = abs(closest.remaining_seconds - remaining_seconds)
        if clock_distance <= max_extrapolation_seconds:
            direction = closest.remaining_seconds - remaining_seconds
            return closest.video_seconds + direction, "extrapolated", closest.run_id
    return None, "unmapped", ""


def synchronize_shots(
    shots: Iterable[Dict[str, Any]], anchors: List[Anchor], config: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    before = float(config["clip"]["seconds_before"])
    after = float(config["clip"]["seconds_after"])
    max_extrapolation = float(config["clock"]["max_extrapolation_seconds"])
    output = []
    status_counts: Dict[str, int] = {}
    for shot in shots:
        video_seconds, status, run_id = map_clock_to_video(
            anchors,
            int(shot["period"]),
            int(shot["remaining_seconds"]),
            max_extrapolation,
        )
        row = dict(shot)
        row["sync_status"] = status
        row["sync_run_id"] = run_id
        if video_seconds is None:
            row.update(
                {
                    "video_seconds": "",
                    "clip_start_seconds": "",
                    "clip_end_seconds": "",
                    "review_url": "",
                }
            )
        else:
            row.update(
                {
                    "video_seconds": round(video_seconds, 3),
                    "clip_start_seconds": round(max(0.0, video_seconds - before), 3),
                    "clip_end_seconds": round(video_seconds + after, 3),
                    "review_url": youtube_timestamp_url(config["video_url"], video_seconds),
                }
            )
        status_counts[status] = status_counts.get(status, 0) + 1
        output.append(row)
    return output, {"shot_count": len(output), "sync_status_counts": status_counts}


def anchors_as_rows(anchors: Iterable[Anchor]) -> List[Dict[str, Any]]:
    return [
        {
            "period": anchor.period,
            "game_clock": format_clock(anchor.remaining_seconds),
            "remaining_seconds": anchor.remaining_seconds,
            "video_seconds": round(anchor.video_seconds, 3),
            "clock_running": anchor.clock_running,
            "confidence": anchor.confidence,
            "run_id": anchor.run_id,
            "source": anchor.source,
        }
        for anchor in anchors
    ]
