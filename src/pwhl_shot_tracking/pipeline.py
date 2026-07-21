from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .gemini import (
    PROMPT_SCHEMA_VERSION,
    TAG_SCHEMA,
    VERIFY_SCHEMA,
    VERIFY_SCHEMA_VERSION,
    GeminiClient,
    blind_verification_prompt,
    contextual_prompt,
)
from .utils import optional_float, read_json, write_json
from .validation import feed_shot_zone, flatten_tagged_row


OFFSET_TOLERANCE_SECONDS = 0.25


def sidecar_is_current(audit: Dict[str, Any], start_seconds: float, end_seconds: float) -> bool:
    """A completed sidecar only counts as a cache hit while its clip offsets
    still match the current sync; re-syncing (merged anchors, new runs) moves
    offsets and must trigger a fresh analysis rather than reattach stale one."""
    if audit.get("status") != "complete":
        return False
    offsets = audit.get("offsets") or {}
    recorded_start = optional_float(offsets.get("start_seconds"))
    recorded_end = optional_float(offsets.get("end_seconds"))
    return (
        recorded_start is not None
        and recorded_end is not None
        and abs(recorded_start - start_seconds) <= OFFSET_TOLERANCE_SECONDS
        and abs(recorded_end - end_seconds) <= OFFSET_TOLERANCE_SECONDS
    )


def _existing_result(
    path: Path, start_seconds: float, end_seconds: float
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    if not path.exists():
        return None
    audit = read_json(path)
    if not sidecar_is_current(audit, start_seconds, end_seconds):
        return None
    tag = audit.get("passes", {}).get("contextual", {}).get("parsed_response")
    verification = audit.get("passes", {}).get("blind_verification", {}).get("parsed_response")
    if isinstance(tag, dict) and isinstance(verification, dict):
        return tag, verification
    return None


def tag_shots(
    shots: Iterable[Dict[str, Any]],
    config: Dict[str, Any],
    client: GeminiClient,
    feed_sha256: str,
    clips_dir: Path,
    force: bool = False,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    input_shots = list(shots)
    synchronized = [row for row in input_shots if row.get("clip_start_seconds") not in ("", None)]
    if limit is not None:
        synchronized = synchronized[:limit]
    output = []
    failures = []
    cached_count = 0
    api_call_count = 0
    for shot in synchronized:
        sidecar_path = clips_dir / ("%s.json" % shot["shot_id"])
        start = float(shot["clip_start_seconds"])
        end = float(shot["clip_end_seconds"])
        existing = None if force else _existing_result(sidecar_path, start, end)
        if existing is not None:
            tag, verification = existing
            cached_count += 1
        else:
            duration = end - start
            audit = {
                "audit_record_version": "1.0",
                "status": "in_progress",
                "shot_id": shot["shot_id"],
                "model_id": config["gemini"]["model"],
                "api_surface": config["gemini"]["api_surface"],
                "source_url": config["video_url"],
                "feed_snapshot_sha256": feed_sha256,
                "offsets": {"start_seconds": start, "end_seconds": end},
                "fps": config["gemini"]["fps"],
                "media_resolution": config["gemini"]["media_resolution"],
                "passes": {},
                "manual_disposition": "pending",
            }
            try:
                tag, tag_audit = client.generate(
                    contextual_prompt(shot, feed_shot_zone(shot)),
                    TAG_SCHEMA,
                    start,
                    end,
                )
                api_call_count += 1
                audit["passes"]["contextual"] = {
                    "prompt_schema_version": PROMPT_SCHEMA_VERSION,
                    "parsed_response": tag,
                    "api_audit": tag_audit,
                }
                write_json(sidecar_path, audit)

                verification, verification_audit = client.generate(
                    blind_verification_prompt(duration),
                    VERIFY_SCHEMA,
                    start,
                    end,
                )
                api_call_count += 1
                audit["passes"]["blind_verification"] = {
                    "prompt_schema_version": VERIFY_SCHEMA_VERSION,
                    "parsed_response": verification,
                    "api_audit": verification_audit,
                }
                audit["status"] = "complete"
                write_json(sidecar_path, audit)
            except Exception as error:
                audit["status"] = "failed"
                audit["error"] = "%s: %s" % (type(error).__name__, error)
                if getattr(error, "audit", None):
                    audit["failure_api_audit"] = error.audit
                write_json(sidecar_path, audit)
                failures.append({"shot_id": shot["shot_id"], "error": audit["error"]})
                continue
        output.append(
            flatten_tagged_row(
                shot,
                tag,
                verification,
                float(shot["clip_end_seconds"]) - float(shot["clip_start_seconds"]),
            )
        )
    report = {
        "input_shot_count": len(input_shots),
        "eligible_shot_count": len(synchronized),
        "tagged_count": len(output),
        "cached_count": cached_count,
        "api_call_count": api_call_count,
        "failure_count": len(failures),
        "failures": failures,
    }
    return output, report
