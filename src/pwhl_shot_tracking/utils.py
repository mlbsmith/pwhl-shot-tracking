import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def parse_clock(value: Any) -> int:
    """Parse MM:SS, HH:MM:SS, or numeric seconds.

    Broadcast scorebugs switch to tenths under a minute (':52.9', '17.9',
    '0:45.5'), so a leading ':' is stripped and the final seconds component
    may carry a decimal fraction, which is truncated toward zero.
    """
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("clock value is empty")
    if text.startswith(":"):
        text = text[1:]
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    seconds_part = parts[-1]
    seconds_is_valid = seconds_part.isdigit() or (
        seconds_part.count(".") == 1
        and seconds_part.replace(".", "").isdigit()
        and not seconds_part.startswith(".")
    )
    if not seconds_is_valid or not all(part.isdigit() for part in parts[:-1]):
        raise ValueError("invalid clock value: %s" % text)
    seconds = int(float(seconds_part))
    if len(parts) == 1:
        return seconds
    if len(parts) == 2:
        return int(parts[0]) * 60 + seconds
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + seconds
    raise ValueError("invalid clock value: %s" % text)


def format_clock(seconds: int) -> str:
    seconds = max(0, int(round(seconds)))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(path, payload)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    materialized = list(rows)
    if fieldnames is None:
        fieldnames = []
        seen = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(materialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def youtube_timestamp_url(video_url: str, seconds: float) -> str:
    parsed = urlparse(video_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["t"] = "%ds" % max(0, int(round(seconds)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def api_keys_from_environment() -> List[str]:
    keys = []
    single = os.environ.get("GEMINI_API_KEY", "").strip()
    if single:
        keys.append(single)
    multiple = os.environ.get("GEMINI_API_KEYS", "")
    for key in multiple.split(","):
        key = key.strip()
        if key and key not in keys:
            keys.append(key)
    return keys
