import json
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .clock import Anchor
from .utils import api_keys_from_environment, parse_clock


PROMPT_SCHEMA_VERSION = "royal-road-v1.0"
VERIFY_SCHEMA_VERSION = "blind-clip-check-v1.0"
ANCHOR_SCHEMA_VERSION = "scorebug-anchors-v1.0"

CONFIDENCE = ["high", "medium", "low"]
SHOT_ZONES = [
    "goal_line",
    "low_slot",
    "high_slot",
    "left_circle",
    "right_circle",
    "left_point",
    "right_point",
    "neutral_or_far",
    "unknown",
]


TAG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "royal_road": {"type": "boolean"},
        "royal_road_confidence": {"type": "string", "enum": CONFIDENCE},
        "pass_direction": {
            "type": "string",
            "enum": ["left_to_right", "right_to_left", "none", "unclear"],
        },
        "entry_type": {"type": "string", "enum": ["controlled", "dump", "none", "unclear"]},
        "screen": {"type": "boolean"},
        "rebound": {"type": "boolean"},
        "rush_vs_cycle": {"type": "string", "enum": ["rush", "cycle", "unclear"]},
        "shooter_number": {"type": ["string", "null"]},
        "passer_number": {"type": ["string", "null"]},
        "passer_name": {"type": ["string", "null"]},
        "passer_id_source": {"type": "string", "enum": ["jersey", "commentary", "both", "none"]},
        "passer_confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "shot_zone": {"type": "string", "enum": SHOT_ZONES},
        "pass_origin_longitudinal_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "pass_origin_lateral_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "pass_crossing_longitudinal_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "pass_geometry_confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "rationale": {"type": "string"},
    },
    "required": [
        "royal_road",
        "royal_road_confidence",
        "pass_direction",
        "entry_type",
        "screen",
        "rebound",
        "rush_vs_cycle",
        "shooter_number",
        "passer_number",
        "passer_name",
        "passer_id_source",
        "passer_confidence",
        "shot_zone",
        "pass_origin_longitudinal_pct",
        "pass_origin_lateral_pct",
        "pass_crossing_longitudinal_pct",
        "pass_geometry_confidence",
        "rationale",
    ],
}


VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clip_valid": {"type": "boolean"},
        "shot_seen_at_seconds": {"type": ["number", "null"], "minimum": 0, "maximum": 30},
        "shooter_number": {"type": ["string", "null"]},
        "shot_zone": {"type": "string", "enum": SHOT_ZONES},
        "confidence": {"type": "string", "enum": CONFIDENCE},
        "rationale": {"type": "string"},
    },
    "required": [
        "clip_valid",
        "shot_seen_at_seconds",
        "shooter_number",
        "shot_zone",
        "confidence",
        "rationale",
    ],
}


ANCHOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "anchors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "period": {"type": "integer", "minimum": 1, "maximum": 20},
                    "game_clock": {"type": "string"},
                    "seconds_from_clip_start": {"type": "number", "minimum": 0},
                    "clock_running": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": CONFIDENCE},
                },
                "required": [
                    "period",
                    "game_clock",
                    "seconds_from_clip_start",
                    "clock_running",
                    "confidence",
                ],
            },
        }
    },
    "required": ["anchors"],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_duration(value: float) -> str:
    text = ("%.3f" % float(value)).rstrip("0").rstrip(".")
    return text + "s"


def contextual_prompt(shot: Dict[str, Any], feed_zone: str) -> str:
    return """You are analyzing a short broadcast clip ending just after one expected hockey shot.

ROYAL ROAD = a pass that crosses the imaginary net-to-net center line, below the tops
of the offensive-zone faceoff circles, immediately before the shot. A carry by the
shooter is not a pass. A pass above the circle tops is not royal road.

Analyze the final attacking sequence only. Use audio and visible jerseys for passer
attribution, but never guess a player. Null means not observed. The normalized
attacking frame has longitudinal 0 at the goal line and 100 at the blue line; lateral
0 is the attacker's left boards and 100 is the attacker's right boards. Only provide
pass geometry when visible; otherwise return nulls and confidence "none".

Known feed context (use it to identify the intended shot, not as visual evidence):
- team: {team}
- shooter: #{number} {name}
- period: {period}
- game clock remaining: {clock}
- feed shot zone: {zone}

Return only the structured result. Keep rationale to one sentence.
""".format(
        team=shot.get("team_code", ""),
        number=shot.get("shooter_number", ""),
        name=shot.get("shooter_name", ""),
        period=shot.get("period", ""),
        clock=shot.get("time_remaining", ""),
        zone=feed_zone,
    )


def blind_verification_prompt(clip_duration: float) -> str:
    return """Independently verify this hockey clip without relying on feed metadata.
Do not infer a player, team, clock, or location from this prompt.

Determine whether one live-play shot is visible near the end of the clip. Replays,
crowd shots, stoppages, and shots far from the expected end make clip_valid false.
Report the shot time in seconds from the beginning of this supplied clip, the visible
shooter jersey number if readable, and a normalized shot zone. The clip is %.3f seconds
long. Return only the structured result and keep rationale to one sentence.
""" % clip_duration


def anchor_prompt(clip_duration: float) -> str:
    return """Read the broadcast scorebug throughout this video interval.

Return observations of the period and countdown game clock. Record an anchor about
every 10 seconds while the clock is visibly running and at the start/end of each
active-clock run. Do not interpolate or invent unreadable values. Repeated clock
values during whistles must be marked clock_running=false. seconds_from_clip_start is
the timestamp within this supplied %.3f-second interval, not the full source video.
Return only the structured result.
""" % clip_duration


class GeminiError(RuntimeError):
    def __init__(self, message: str, audit: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.audit = audit


class GeminiClient:
    def __init__(
        self,
        config: Dict[str, Any],
        api_keys: Optional[List[str]] = None,
        opener: Optional[Callable[..., Any]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.settings = config["gemini"]
        self.api_keys = list(api_keys if api_keys is not None else api_keys_from_environment())
        if not self.api_keys:
            raise GeminiError("set GEMINI_API_KEY or comma-separated GEMINI_API_KEYS")
        self.opener = opener or urlopen
        self.sleeper = sleeper
        self._key_cursor = 0

    def build_request(
        self,
        prompt: str,
        schema: Dict[str, Any],
        start_seconds: float,
        end_seconds: float,
        fps: float,
        media_resolution: str,
    ) -> Dict[str, Any]:
        if end_seconds <= start_seconds:
            raise ValueError("clip end must be after clip start")
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "fileData": {
                                "fileUri": self.config["video_url"],
                                "mimeType": "video/*",
                            },
                            "videoMetadata": {
                                "startOffset": _seconds_duration(start_seconds),
                                "endOffset": _seconds_duration(end_seconds),
                                "fps": float(fps),
                            },
                        },
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0.1,
                "mediaResolution": media_resolution,
            },
        }

    def generate(
        self,
        prompt: str,
        schema: Dict[str, Any],
        start_seconds: float,
        end_seconds: float,
        fps: Optional[float] = None,
        media_resolution: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        fps = float(fps if fps is not None else self.settings["fps"])
        media_resolution = media_resolution or self.settings["media_resolution"]
        body = self.build_request(prompt, schema, start_seconds, end_seconds, fps, media_resolution)
        model = str(self.settings["model"])
        endpoint = "%s/models/%s:generateContent" % (
            str(self.settings["base_url"]).rstrip("/"),
            quote(model, safe="-._"),
        )
        attempts = []
        max_attempts = int(self.settings["max_attempts"])
        for attempt_number in range(1, max_attempts + 1):
            key_index = (self._key_cursor + attempt_number - 1) % len(self.api_keys)
            started_at = _utc_now()
            try:
                request = Request(
                    endpoint,
                    data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "x-goog-api-key": self.api_keys[key_index],
                        "User-Agent": "pwhl-shot-tracking/0.1",
                    },
                    method="POST",
                )
                with self.opener(request, timeout=float(self.settings["timeout_seconds"])) as response:
                    raw_bytes = response.read()
                    status = getattr(response, "status", 200)
                raw = json.loads(raw_bytes.decode("utf-8"))
                parsed = self._parse_response(raw)
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "key_index": key_index,
                        "started_at": started_at,
                        "finished_at": _utc_now(),
                        "http_status": status,
                        "outcome": "success",
                        "raw_response": raw,
                    }
                )
                self._key_cursor = (key_index + 1) % len(self.api_keys)
                return parsed, {"request": body, "endpoint": endpoint, "attempts": attempts}
            except HTTPError as error:
                try:
                    error_body = error.read().decode("utf-8", errors="replace")
                except Exception:
                    error_body = ""
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "key_index": key_index,
                        "started_at": started_at,
                        "finished_at": _utc_now(),
                        "http_status": error.code,
                        "outcome": "http_error",
                        "error": error_body,
                    }
                )
                if error.code not in {408, 429, 500, 502, 503, 504} or attempt_number == max_attempts:
                    raise GeminiError(
                        "Gemini HTTP %s: %s" % (error.code, error_body[:500]),
                        {"request": body, "endpoint": endpoint, "attempts": attempts},
                    )
            except (URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError, GeminiError) as error:
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "key_index": key_index,
                        "started_at": started_at,
                        "finished_at": _utc_now(),
                        "outcome": "error",
                        "error": "%s: %s" % (type(error).__name__, error),
                    }
                )
                if attempt_number == max_attempts:
                    raise GeminiError(
                        "Gemini request failed: %s" % error,
                        {"request": body, "endpoint": endpoint, "attempts": attempts},
                    )
            delay = min(20.0, (2 ** (attempt_number - 1)) + random.random())
            self.sleeper(delay)
        raise GeminiError(
            "Gemini request exhausted retries",
            {"request": body, "endpoint": endpoint, "attempts": attempts},
        )

    @staticmethod
    def _parse_response(raw: Dict[str, Any]) -> Dict[str, Any]:
        candidates = raw.get("candidates") or []
        if not candidates:
            raise GeminiError("Gemini response has no candidates: %s" % raw.get("promptFeedback", {}))
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(part.get("text") or "") for part in parts).strip()
        if not text:
            raise GeminiError("Gemini response candidate has no text")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise GeminiError("Gemini structured response is not an object")
        return parsed


def discover_anchors(
    client: GeminiClient,
    start_seconds: float,
    end_seconds: float,
) -> Tuple[List[Anchor], Dict[str, Any]]:
    duration = end_seconds - start_seconds
    result, audit = client.generate(
        anchor_prompt(duration),
        ANCHOR_SCHEMA,
        start_seconds,
        end_seconds,
        fps=1.0,
        media_resolution="MEDIA_RESOLUTION_HIGH",
    )
    anchors = []
    rejected = []
    for item in result.get("anchors", []):
        try:
            relative_seconds = float(item["seconds_from_clip_start"])
            if relative_seconds < 0 or relative_seconds > duration + 1:
                raise ValueError("relative timestamp outside clip")
            anchors.append(
                Anchor(
                    period=int(item["period"]),
                    remaining_seconds=parse_clock(item["game_clock"]),
                    video_seconds=start_seconds + relative_seconds,
                    clock_running=bool(item["clock_running"]),
                    confidence=str(item["confidence"]),
                    source="gemini",
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            rejected.append({"item": item, "error": str(error)})
    audit["parsed_response"] = result
    audit["rejected_anchors"] = rejected
    audit["schema_version"] = ANCHOR_SCHEMA_VERSION
    return anchors, audit
