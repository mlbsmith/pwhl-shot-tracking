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


def _anchor_from_row(row: Dict[str, Any]) -> Anchor:
    game_clock = row.get("game_clock") or row.get("time_remaining") or row.get("remaining_seconds")
    if not game_clock:
        raise ValueError("anchor CSV requires game_clock (time remaining)")
    return Anchor(
        period=int(row["period"]),
        remaining_seconds=parse_clock(game_clock),
        video_seconds=float(row["video_seconds"]),
        clock_running=as_bool(row.get("clock_running", "true")),
        confidence=str(row.get("confidence") or "manual"),
        run_id=str(row.get("run_id") or ""),
        source=str(row.get("source") or "manual"),
    )


def read_anchor_rows(path: Path) -> List[Anchor]:
    return [_anchor_from_row(row) for row in read_csv(path)]


def load_anchors(path: Path, max_stoppage_gap_seconds: float = 3.0) -> List[Anchor]:
    return assign_active_runs(read_anchor_rows(path), max_stoppage_gap_seconds)


def merge_anchors(*anchor_groups: Iterable[Anchor]) -> List[Anchor]:
    """Union anchors from several scans into one deduplicated, unassigned list.

    Discovered run ids are meaningless across scans, so only manual anchors keep
    their explicit run_id; everything else is reassigned by assign_active_runs.
    """
    merged: Dict[Tuple[int, int, int, bool], Anchor] = {}
    for group in anchor_groups:
        for anchor in group:
            if anchor.source != "manual":
                anchor = replace(anchor, run_id="")
            key = (
                anchor.period,
                anchor.remaining_seconds,
                int(round(anchor.video_seconds)),
                anchor.clock_running,
            )
            existing = merged.get(key)
            if existing is None or (anchor.source == "manual" and existing.source != "manual"):
                merged[key] = anchor
    return sorted(merged.values(), key=lambda item: item.video_seconds)


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
            # clock_delta == 0 is a repeat reading of the same displayed second;
            # the stoppage-gap rule below still splits when the clock has stalled.
            # The clock outpacing video means a broadcast cut, so that splits too.
            starts_new = (
                anchor.period != previous.period
                or video_delta <= 0
                or clock_delta < 0
                or abs(video_delta - clock_delta) > max_stoppage_gap_seconds
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


def sync_gap_report(
    synced_rows: Iterable[Dict[str, Any]],
    anchors: List[Anchor],
    video_duration_seconds: Optional[float] = None,
    pad_seconds: float = 10.0,
    unbounded_slack_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Describe anchor-coverage gaps behind unmapped shots and suggest targeted scans.

    Each unmapped shot is bracketed by its nearest same-period anchors. The video
    interval between those anchors necessarily contains the shot, so re-running
    discover-anchors on just that interval is enough to recover it. When a side is
    unbounded, the window extends by the clock distance plus a slack allowance,
    because stoppages can push the shot later than the clock difference alone.
    """
    gaps = []
    for row in synced_rows:
        if row.get("sync_status") != "unmapped":
            continue
        period = int(row["period"])
        remaining = int(row["remaining_seconds"])
        period_anchors = [anchor for anchor in anchors if anchor.period == period]
        before = [anchor for anchor in period_anchors if anchor.remaining_seconds > remaining]
        after = [anchor for anchor in period_anchors if anchor.remaining_seconds < remaining]
        anchor_before = min(before, key=lambda item: item.remaining_seconds) if before else None
        anchor_after = max(after, key=lambda item: item.remaining_seconds) if after else None
        start = end = None
        if anchor_before is not None:
            start = anchor_before.video_seconds
            end = anchor_before.video_seconds + (
                anchor_before.remaining_seconds - remaining
            ) + unbounded_slack_seconds
        if anchor_after is not None:
            end = anchor_after.video_seconds
            if start is None:
                start = anchor_after.video_seconds - (
                    remaining - anchor_after.remaining_seconds
                ) - unbounded_slack_seconds
        if start is not None:
            start = max(0.0, start - pad_seconds)
        if end is not None:
            end = end + pad_seconds
            if video_duration_seconds is not None:
                end = min(float(video_duration_seconds), end)
        gaps.append(
            {
                "shot_id": row.get("shot_id", ""),
                "period": period,
                "time_remaining": format_clock(remaining),
                "anchor_before": None
                if anchor_before is None
                else {
                    "game_clock": format_clock(anchor_before.remaining_seconds),
                    "video_seconds": round(anchor_before.video_seconds, 3),
                },
                "anchor_after": None
                if anchor_after is None
                else {
                    "game_clock": format_clock(anchor_after.remaining_seconds),
                    "video_seconds": round(anchor_after.video_seconds, 3),
                },
                "scan_start_seconds": None if start is None else round(start, 3),
                "scan_end_seconds": None if end is None else round(end, 3),
            }
        )

    windows = sorted(
        (
            (gap["scan_start_seconds"], gap["scan_end_seconds"], gap["shot_id"])
            for gap in gaps
            if gap["scan_start_seconds"] is not None and gap["scan_end_seconds"] is not None
        ),
    )
    scans: List[Dict[str, Any]] = []
    for start, end, shot_id in windows:
        if scans and start <= scans[-1]["end_seconds"] + 30.0:
            scans[-1]["end_seconds"] = max(scans[-1]["end_seconds"], end)
            scans[-1]["shot_ids"].append(shot_id)
        else:
            scans.append({"start_seconds": start, "end_seconds": end, "shot_ids": [shot_id]})
    for scan in scans:
        scan["command"] = (
            "python3 -m pwhl_shot_tracking discover-anchors --config game.json"
            " --start %.0f --end %.0f --yes" % (scan["start_seconds"], scan["end_seconds"])
        )
    return {"unmapped_count": len(gaps), "gaps": gaps, "suggested_scans": scans}


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
