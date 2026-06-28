from typing import Any, Dict, Iterable, List, Optional, Tuple

from .utils import as_bool, optional_float


def feed_shot_zone(row: Dict[str, Any]) -> str:
    x = optional_float(row.get("x"))
    y = optional_float(row.get("y"))
    if x is None or y is None:
        return "unknown"
    attacks_left = x < 300
    goal_x = 60.0 if attacks_left else 540.0
    longitudinal = abs(x - goal_x)
    lateral_signed = y - 150.0
    lateral = abs(lateral_signed)
    if longitudinal <= 20 and lateral > 55:
        return "goal_line"
    if longitudinal <= 80 and lateral <= 55:
        return "low_slot"
    if longitudinal <= 150 and lateral <= 72:
        return "high_slot"
    side = "left" if lateral_signed < 0 else "right"
    if longitudinal <= 175:
        return side + "_circle"
    if longitudinal <= 240:
        return side + "_point"
    return "neutral_or_far"


def _zone_family(zone: str) -> str:
    if zone in {"low_slot", "high_slot"}:
        return "slot"
    if zone in {"left_circle", "right_circle"}:
        return "circle"
    if zone in {"left_point", "right_point"}:
        return "point"
    return zone


def automatic_flags(
    row: Dict[str, Any],
    tag: Dict[str, Any],
    verification: Dict[str, Any],
    clip_duration: float,
) -> List[str]:
    reasons = []
    if row.get("sync_status") in {"unmapped", "extrapolated"}:
        reasons.append("sync_%s" % row.get("sync_status"))
    if not verification.get("clip_valid", False):
        reasons.append("blind_check_invalid_clip")
    feed_number = str(row.get("shooter_number") or "").lstrip("0")
    blind_number = str(verification.get("shooter_number") or "").lstrip("0")
    if feed_number and blind_number and feed_number != blind_number:
        reasons.append("shooter_number_mismatch")
    feed_zone = feed_shot_zone(row)
    blind_zone = str(verification.get("shot_zone") or "unknown")
    if (
        feed_zone != "unknown"
        and blind_zone != "unknown"
        and _zone_family(feed_zone) != _zone_family(blind_zone)
    ):
        reasons.append("shot_zone_mismatch")
    shot_seen = optional_float(verification.get("shot_seen_at_seconds"))
    expected = clip_duration - 2.0
    if shot_seen is not None and abs(shot_seen - expected) > 3.0:
        reasons.append("shot_timing_mismatch")
    contextual_number = str(tag.get("shooter_number") or "").lstrip("0")
    if feed_number and contextual_number and feed_number != contextual_number:
        reasons.append("contextual_shooter_mismatch")
    return reasons


def flatten_tagged_row(
    shot: Dict[str, Any],
    tag: Dict[str, Any],
    verification: Dict[str, Any],
    clip_duration: float,
) -> Dict[str, Any]:
    row = dict(shot)
    row["feed_shot_zone"] = feed_shot_zone(shot)
    for key, value in tag.items():
        row["tag_" + key] = value
    for key, value in verification.items():
        row["verify_" + key] = value
    flags = automatic_flags(shot, tag, verification, clip_duration)
    row["flagged"] = bool(flags)
    row["flag_reasons"] = "|".join(flags)
    return row


def review_rows(tagged_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for tagged in tagged_rows:
        rows.append(
            {
                "shot_id": tagged["shot_id"],
                "period": tagged["period"],
                "time_remaining": tagged["time_remaining"],
                "team_code": tagged["team_code"],
                "shooter_number": tagged["shooter_number"],
                "shooter_name": tagged["shooter_name"],
                "model_royal_road": tagged.get("tag_royal_road", ""),
                "model_confidence": tagged.get("tag_royal_road_confidence", ""),
                "flagged": tagged.get("flagged", ""),
                "flag_reasons": tagged.get("flag_reasons", ""),
                "review_url": tagged.get("review_url", ""),
                "manual_royal_road": "",
                "manual_disposition": "pending",
                "manual_notes": "",
            }
        )
    return rows


def finalize_rows(
    tagged_rows: Iterable[Dict[str, Any]],
    manual_rows: Iterable[Dict[str, Any]],
    minimum_precision: float,
    minimum_recall: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    manual_by_id = {row["shot_id"]: row for row in manual_rows}
    output = []
    tp = fp = fn = tn = 0
    pending = []
    for tagged in tagged_rows:
        row = dict(tagged)
        manual = manual_by_id.get(str(tagged["shot_id"]), {})
        disposition = str(manual.get("manual_disposition") or "pending").strip().lower()
        label_text = str(manual.get("manual_royal_road") or "").strip().lower()
        reviewed = disposition in {"confirmed", "corrected"} and label_text in {
            "1",
            "0",
            "true",
            "false",
            "yes",
            "no",
            "y",
            "n",
        }
        if not reviewed:
            pending.append(str(tagged["shot_id"]))
            final_label: Any = ""
        else:
            final_label = as_bool(label_text)
            model_label = as_bool(tagged.get("tag_royal_road"))
            if model_label and final_label:
                tp += 1
            elif model_label and not final_label:
                fp += 1
            elif not model_label and final_label:
                fn += 1
            else:
                tn += 1
        row["manual_disposition"] = disposition
        row["manual_notes"] = manual.get("manual_notes", "")
        row["final_royal_road"] = final_label
        output.append(row)

    precision: Optional[float] = tp / float(tp + fp) if (tp + fp) else None
    recall: Optional[float] = tp / float(tp + fn) if (tp + fn) else None
    complete = not pending
    passes = (
        complete
        and precision is not None
        and recall is not None
        and precision >= minimum_precision
        and recall >= minimum_recall
    )
    report = {
        "status": "publishable" if passes else "draft",
        "review_complete": complete,
        "pending_count": len(pending),
        "pending_shot_ids": pending,
        "confusion_matrix": {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn},
        "precision": precision,
        "recall": recall,
        "minimum_precision": minimum_precision,
        "minimum_recall": minimum_recall,
        "thresholds_passed": passes,
    }
    return output, report
