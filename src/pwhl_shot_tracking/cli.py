import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .clock import anchors_as_rows, assign_active_runs, load_anchors, synchronize_shots
from .config import SHOT_UNIVERSES, create_config, load_config, work_path
from .feed import fetch_game_feed, normalize_shots
from .gemini import GeminiClient, discover_anchors
from .pipeline import tag_shots
from .render import render_shot_map
from .utils import (
    api_keys_from_environment,
    as_bool,
    read_csv,
    read_json,
    write_csv,
    write_json,
)
from .validation import finalize_rows, review_rows


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config(args: argparse.Namespace) -> Dict[str, Any]:
    return load_config(Path(args.config))


def _tagged_path(config: Dict[str, Any]) -> Path:
    return work_path(config, "royal_road_%s.csv" % config["game_id"])


def command_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        raise FileExistsError("%s already exists; use --force to replace it" % path)
    create_config(path, args.game_id, args.video_url, args.shot_universe)
    print("Created %s" % path)
    return 0


def command_fetch(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.source_json:
        source_path = Path(args.source_json)
        payload = read_json(source_path)
        source = str(source_path.resolve())
    else:
        payload, source = fetch_game_feed(config)
    shots, report = normalize_shots(payload, config)
    report.update({"source": source, "fetched_at": _utc_now()})
    write_json(work_path(config, "feed", "raw.json"), payload)
    write_json(work_path(config, "feed", "metadata.json"), report)
    write_csv(work_path(config, "shots.csv"), shots)
    print(
        "Wrote %d candidates (%s); coordinate coverage %.1f%%"
        % (len(shots), config["shot_universe"], report["coordinate_coverage"] * 100)
    )
    if report["coordinate_coverage"] < 0.90:
        print("WARNING: coordinate coverage is below 90%; rendering will use only located shots", file=sys.stderr)
    return 0


def command_discover_anchors(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.end <= args.start:
        raise ValueError("--end must be greater than --start")
    if args.chunk_seconds <= 0 or args.chunk_seconds > 600:
        raise ValueError("--chunk-seconds must be between 1 and 600")
    call_count = int(math.ceil((args.end - args.start) / args.chunk_seconds))
    cap = int(config["gemini"]["max_api_calls_per_run"])
    if call_count > cap:
        raise ValueError("anchor discovery needs %d calls, above configured cap %d" % (call_count, cap))
    if not args.yes:
        raise ValueError("anchor discovery makes %d Gemini calls; rerun with --yes" % call_count)
    client = GeminiClient(config) if calls else None
    anchors = []
    audits = []
    cursor = float(args.start)
    while cursor < args.end:
        chunk_end = min(float(args.end), cursor + float(args.chunk_seconds))
        discovered, audit = discover_anchors(client, cursor, chunk_end)
        anchors.extend(discovered)
        audit_path = work_path(
            config,
            "sync",
            "audit",
            "%09.3f-%09.3f.json" % (cursor, chunk_end),
        )
        write_json(audit_path, audit)
        audits.append(str(audit_path))
        print("Discovered %d anchors in %.1f-%.1fs" % (len(discovered), cursor, chunk_end))
        cursor = chunk_end
    normalized = assign_active_runs(anchors, float(config["clock"]["max_stoppage_gap_seconds"]))
    output_path = Path(args.output) if args.output else work_path(config, "sync", "anchors.csv")
    write_csv(output_path, anchors_as_rows(normalized))
    write_json(
        work_path(config, "sync", "discovery_report.json"),
        {
            "created_at": _utc_now(),
            "range": {"start_seconds": args.start, "end_seconds": args.end},
            "chunk_seconds": args.chunk_seconds,
            "api_call_count": call_count,
            "anchor_count": len(normalized),
            "audit_files": audits,
        },
    )
    print("Wrote %d anchors to %s" % (len(normalized), output_path))
    return 0


def command_sync(args: argparse.Namespace) -> int:
    config = _config(args)
    shots_path = Path(args.shots) if args.shots else work_path(config, "shots.csv")
    anchors_path = Path(args.anchors) if args.anchors else work_path(config, "sync", "anchors.csv")
    shots = read_csv(shots_path)
    anchors = load_anchors(anchors_path, float(config["clock"]["max_stoppage_gap_seconds"]))
    synchronized, report = synchronize_shots(shots, anchors, config)
    report.update({"created_at": _utc_now(), "anchor_count": len(anchors), "anchors_source": str(anchors_path)})
    write_csv(work_path(config, "shots_synced.csv"), synchronized)
    write_csv(work_path(config, "sync", "anchors_normalized.csv"), anchors_as_rows(anchors))
    write_json(work_path(config, "sync", "report.json"), report)
    print("Sync status: %s" % report["sync_status_counts"])
    if report["sync_status_counts"].get("unmapped", 0):
        print("WARNING: unmapped shots cannot be tagged; add anchors for their active-clock runs", file=sys.stderr)
    return 0


def _selected_rows(rows: List[Dict[str, Any]], limit: Optional[int]) -> List[Dict[str, Any]]:
    return rows if limit is None else rows[:limit]


def _feed_hash(config: Dict[str, Any]) -> str:
    metadata_path = work_path(config, "feed", "metadata.json")
    return str(read_json(metadata_path).get("feed_sha256") or "")


def _pending_api_calls(rows: List[Dict[str, Any]], config: Dict[str, Any], force: bool) -> int:
    pending = 0
    for row in rows:
        if row.get("clip_start_seconds") in ("", None):
            continue
        sidecar = work_path(config, "clips", "%s.json" % row["shot_id"])
        complete = False
        if sidecar.exists() and not force:
            complete = read_json(sidecar).get("status") == "complete"
        if not complete:
            pending += 2
    return pending


def _guard_gemini_calls(call_count: int, config: Dict[str, Any], confirmed: bool) -> None:
    cap = int(config["gemini"]["max_api_calls_per_run"])
    if call_count > cap:
        raise ValueError("run needs up to %d calls, above configured cap %d" % (call_count, cap))
    if call_count and not confirmed:
        raise ValueError("run can make %d Gemini calls; rerun with --yes" % call_count)


def command_tag(args: argparse.Namespace) -> int:
    config = _config(args)
    rows = _selected_rows(read_csv(work_path(config, "shots_synced.csv")), args.limit)
    calls = _pending_api_calls(rows, config, args.force)
    _guard_gemini_calls(calls, config, args.yes)
    client = GeminiClient(config)
    tagged, report = tag_shots(
        rows,
        config,
        client,  # type: ignore
        _feed_hash(config),
        work_path(config, "clips"),
        force=args.force,
        limit=None,
    )
    report["created_at"] = _utc_now()
    write_csv(_tagged_path(config), tagged)
    write_json(work_path(config, "tag_report.json"), report)
    print("Tagged %d shots; %d cached; %d failed" % (len(tagged), report["cached_count"], report["failure_count"]))
    return 1 if report["failure_count"] else 0


def command_calibrate(args: argparse.Namespace) -> int:
    config = _config(args)
    selection = read_csv(Path(args.selection))
    if not selection:
        raise ValueError("calibration selection is empty")
    labels = {as_bool(row.get("expected_royal_road")) for row in selection}
    if labels != {False, True}:
        raise ValueError("calibration must contain both positive and hard-negative examples")
    by_id = {row["shot_id"]: row for row in read_csv(work_path(config, "shots_synced.csv"))}
    selected = []
    expected = {}
    for item in selection:
        shot_id = item["shot_id"]
        if shot_id not in by_id:
            raise ValueError("calibration shot_id not found: %s" % shot_id)
        selected.append(by_id[shot_id])
        expected[shot_id] = as_bool(item.get("expected_royal_road"))
    calls = _pending_api_calls(selected, config, args.force)
    _guard_gemini_calls(calls, config, args.yes)
    client = GeminiClient(config) if calls else None
    tagged, tag_report = tag_shots(
        selected,
        config,
        client,  # type: ignore
        _feed_hash(config),
        work_path(config, "clips"),
        force=args.force,
    )
    tp = fp = fn = tn = 0
    for row in tagged:
        prediction = as_bool(row.get("tag_royal_road"))
        truth = expected[row["shot_id"]]
        row["expected_royal_road"] = truth
        row["calibration_correct"] = prediction == truth
        if prediction and truth:
            tp += 1
        elif prediction:
            fp += 1
        elif truth:
            fn += 1
        else:
            tn += 1
    precision = tp / float(tp + fp) if (tp + fp) else None
    recall = tp / float(tp + fn) if (tp + fn) else None
    report = {
        "created_at": _utc_now(),
        "selection_count": len(selection),
        "tag_report": tag_report,
        "confusion_matrix": {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn},
        "precision": precision,
        "recall": recall,
    }
    write_csv(work_path(config, "calibration", "results.csv"), tagged)
    write_json(work_path(config, "calibration", "report.json"), report)
    print("Calibration precision=%s recall=%s" % (precision, recall))
    return 0


def command_review(args: argparse.Namespace) -> int:
    config = _config(args)
    generated = review_rows(read_csv(_tagged_path(config)))
    output_path = Path(args.output) if args.output else work_path(config, "manual_review.csv")
    existing = {}
    if output_path.exists():
        existing = {row["shot_id"]: row for row in read_csv(output_path)}
    for row in generated:
        previous = existing.get(row["shot_id"], {})
        for key in ("manual_royal_road", "manual_disposition", "manual_notes"):
            if key in previous:
                row[key] = previous[key]
    write_csv(output_path, generated)
    print("Wrote review sheet with %d candidates to %s" % (len(generated), output_path))
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    config = _config(args)
    manual_path = Path(args.review) if args.review else work_path(config, "manual_review.csv")
    validation = config["validation"]
    tagged_rows = read_csv(_tagged_path(config))
    final, report = finalize_rows(
        tagged_rows,
        read_csv(manual_path),
        float(validation["minimum_precision"]),
        float(validation["minimum_recall"]),
    )
    expected_ids = {row["shot_id"] for row in read_csv(work_path(config, "shots.csv"))}
    tagged_ids = {row["shot_id"] for row in tagged_rows}
    missing_ids = sorted(expected_ids - tagged_ids)
    report["pipeline_complete"] = not missing_ids
    report["expected_candidate_count"] = len(expected_ids)
    report["tagged_candidate_count"] = len(tagged_ids)
    report["missing_candidate_count"] = len(missing_ids)
    report["missing_shot_ids"] = missing_ids
    if missing_ids:
        report["status"] = "draft"
        report["thresholds_passed"] = False

    for row in final:
        sidecar_path = work_path(config, "clips", "%s.json" % row["shot_id"])
        if not sidecar_path.exists():
            continue
        sidecar = read_json(sidecar_path)
        sidecar["manual_disposition"] = row.get("manual_disposition", "pending")
        sidecar["manual_notes"] = row.get("manual_notes", "")
        sidecar["final_royal_road"] = row.get("final_royal_road", "")
        sidecar["reviewed_at"] = _utc_now()
        write_json(sidecar_path, sidecar)

    report["created_at"] = _utc_now()
    write_csv(work_path(config, "final.csv"), final)
    write_json(work_path(config, "validation_report.json"), report)
    print(
        "Validation status: %s; pending=%d precision=%s recall=%s"
        % (report["status"], report["pending_count"], report["precision"], report["recall"])
    )
    if missing_ids:
        print("WARNING: %d denominator candidates have no completed tag" % len(missing_ids), file=sys.stderr)
    return 0 if report["thresholds_passed"] else 1


def command_render(args: argparse.Namespace) -> int:
    config = _config(args)
    final_path = work_path(config, "final.csv")
    if not final_path.exists():
        raise FileNotFoundError("run finalize before render")
    validation_report = read_json(work_path(config, "validation_report.json"))
    publishable = bool(validation_report.get("thresholds_passed"))
    if args.publish and not publishable:
        raise ValueError("publish rendering refused: manual review or validation thresholds are incomplete")
    output_path = (
        Path(args.output)
        if args.output
        else work_path(config, "royal_road_%s.png" % config["game_id"])
    )
    report = render_shot_map(read_csv(final_path), config, output_path, publishable)
    report["created_at"] = _utc_now()
    write_json(work_path(config, "render_report.json"), report)
    print("Wrote %s (%s)" % (output_path, "publishable" if publishable else "DRAFT"))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    checks = {
        "config": "ok",
        "game_id": str(config["game_id"]),
        "shot_universe": config["shot_universe"],
        "gemini_model": config["gemini"]["model"],
        "gemini_api_surface": config["gemini"]["api_surface"],
        "gemini_key_count": len(api_keys_from_environment()),
        "files": {},
    }
    for name, path in [
        ("feed", work_path(config, "feed", "raw.json")),
        ("shots", work_path(config, "shots.csv")),
        ("anchors", work_path(config, "sync", "anchors.csv")),
        ("synced_shots", work_path(config, "shots_synced.csv")),
        ("tagged", _tagged_path(config)),
        ("review", work_path(config, "manual_review.csv")),
        ("final", work_path(config, "final.csv")),
    ]:
        checks["files"][name] = path.exists()
    print_json(checks)
    return 0


def print_json(value: Any) -> None:
    import json

    print(json.dumps(value, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pwhl-shot-tracking")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a game configuration")
    init.add_argument("--config", default="game.json")
    init.add_argument("--game-id", required=True)
    init.add_argument("--video-url", required=True)
    init.add_argument("--shot-universe", choices=sorted(SHOT_UNIVERSES), default="shots_on_goal")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)

    fetch = subparsers.add_parser("fetch", help="fetch and normalize HockeyTech candidates")
    fetch.add_argument("--config", default="game.json")
    fetch.add_argument("--source-json", help="read a saved response instead of using the network")
    fetch.set_defaults(func=command_fetch)

    discover = subparsers.add_parser("discover-anchors", help="read scorebug clocks with Gemini")
    discover.add_argument("--config", default="game.json")
    discover.add_argument("--start", type=float, required=True)
    discover.add_argument("--end", type=float, required=True)
    discover.add_argument("--chunk-seconds", type=float, default=300.0)
    discover.add_argument("--output")
    discover.add_argument("--yes", action="store_true", help="confirm billable Gemini calls")
    discover.set_defaults(func=command_discover_anchors)

    sync = subparsers.add_parser("sync", help="map game clocks to VOD offsets")
    sync.add_argument("--config", default="game.json")
    sync.add_argument("--shots")
    sync.add_argument("--anchors")
    sync.set_defaults(func=command_sync)

    tag = subparsers.add_parser("tag", help="run contextual and blind Gemini passes")
    tag.add_argument("--config", default="game.json")
    tag.add_argument("--limit", type=int)
    tag.add_argument("--force", action="store_true")
    tag.add_argument("--yes", action="store_true", help="confirm billable Gemini calls")
    tag.set_defaults(func=command_tag)

    calibrate = subparsers.add_parser("calibrate", help="test known positives and hard negatives")
    calibrate.add_argument("--config", default="game.json")
    calibrate.add_argument("--selection", required=True)
    calibrate.add_argument("--force", action="store_true")
    calibrate.add_argument("--yes", action="store_true", help="confirm billable Gemini calls")
    calibrate.set_defaults(func=command_calibrate)

    review = subparsers.add_parser("review", help="create or refresh the manual review sheet")
    review.add_argument("--config", default="game.json")
    review.add_argument("--output")
    review.set_defaults(func=command_review)

    finalize = subparsers.add_parser("finalize", help="merge manual labels and calculate validation")
    finalize.add_argument("--config", default="game.json")
    finalize.add_argument("--review")
    finalize.set_defaults(func=command_finalize)

    render = subparsers.add_parser("render", help="render the royal-road shot map")
    render.add_argument("--config", default="game.json")
    render.add_argument("--output")
    render.add_argument("--publish", action="store_true", help="refuse output unless validation passed")
    render.set_defaults(func=command_render)

    doctor = subparsers.add_parser("doctor", help="show configuration and pipeline state")
    doctor.add_argument("--config", default="game.json")
    doctor.set_defaults(func=command_doctor)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError) as error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
