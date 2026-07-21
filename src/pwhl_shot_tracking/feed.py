import json
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .utils import canonical_json_bytes, format_clock, optional_float, parse_clock, sha256_bytes


EVENTS_BY_UNIVERSE = {
    "shots_on_goal": {"shot"},
    "fenwick": {"shot", "missed_shot"},
    "all_attempts": {"shot", "missed_shot", "blocked_shot"},
}


def fetch_game_feed(config: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    hockeytech = config["hockeytech"]
    query = urlencode(
        {
            "feed": "gc",
            "tab": "pxpverbose",
            "game_id": str(config["game_id"]),
            "key": hockeytech["feed_key"],
            "client_code": hockeytech["client_code"],
        }
    )
    url = hockeytech["base_url"] + "?" + query
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "pwhl-shot-tracking/0.1"})
    with urlopen(request, timeout=float(hockeytech["timeout_seconds"])) as response:
        content = response.read()
    payload = json.loads(content.decode("utf-8"))
    return payload, url


def extract_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        payload.get("GC", {}).get("Pxpverbose"),
        payload.get("GC", {}).get("pxpverbose"),
        payload.get("Pxpverbose"),
        payload.get("pxpverbose"),
        payload.get("events"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
    raise ValueError("HockeyTech response does not contain a pxpverbose event list")


def team_jersey_numbers(payload: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Derive each team's jersey numbers from every player reference in the
    play-by-play feed. The pxpverbose tab carries no roster block, but shots,
    goals, faceoffs, hits, and penalties collectively cover the dressed lineup."""
    numbers: Dict[str, Set[str]] = {}
    for event in extract_events(payload):
        references = []
        for key in (
            "player",
            "player1",
            "player2",
            "goal_scorer",
            "assist1_player",
            "assist2_player",
            "goalie",
            "winner",
            "loser",
            "plus",
            "minus",
        ):
            value = event.get(key)
            if isinstance(value, dict):
                references.append(value)
            elif isinstance(value, list):
                references.extend(item for item in value if isinstance(item, dict))
        for reference in references:
            team = str(reference.get("team_id") or "")
            jersey = str(reference.get("jersey_number") or "").lstrip("0")
            if team and jersey:
                numbers.setdefault(team, set()).add(jersey)
        jersey = str(event.get("jersey_number") or "").lstrip("0")
        team = str(event.get("team_id") or "")
        if team and jersey:
            numbers.setdefault(team, set()).add(jersey)
    return numbers


def _period_length(period: int, config: Dict[str, Any]) -> int:
    clock = config["clock"]
    if period <= 3:
        return int(clock["regulation_period_seconds"])
    return int(clock["overtime_period_seconds"])


def _player_fields(event: Dict[str, Any]) -> Tuple[str, str, str]:
    player = event.get("player") or event.get("goal_scorer") or {}
    number = str(player.get("jersey_number") or event.get("jersey_number") or "")
    player_id = str(player.get("player_id") or event.get("player_id") or event.get("goal_player_id") or "")
    name = " ".join(
        part for part in [str(player.get("first_name") or "").strip(), str(player.get("last_name") or "").strip()] if part
    )
    return player_id, number, name


def _event_key(event: Dict[str, Any]) -> Tuple[str, str, str]:
    period = str(event.get("period_id") or event.get("period") or "")
    elapsed = str(event.get("s") if event.get("s") is not None else event.get("seconds") or event.get("time") or "")
    player_id = str(event.get("player_id") or event.get("goal_player_id") or "")
    return period, elapsed, player_id


def normalize_shots(payload: Dict[str, Any], config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    universe = config["shot_universe"]
    allowed = EVENTS_BY_UNIVERSE[universe]
    events = extract_events(payload)
    selected = [event for event in events if str(event.get("event") or "").lower() in allowed]

    # A few historical feeds expose a goal without its companion "shot" row.
    represented = {_event_key(event) for event in selected if event.get("event") == "shot"}
    for event in events:
        if event.get("event") == "goal" and _event_key(event) not in represented:
            selected.append(event)

    shots = []
    seen_ids: Set[str] = set()
    for index, event in enumerate(selected):
        event_type = str(event.get("event") or "shot").lower()
        period = int(event.get("period_id") or event.get("period") or 0)
        if period <= 0:
            continue
        elapsed_raw = event.get("s")
        if elapsed_raw is None:
            elapsed_raw = event.get("seconds")
        if elapsed_raw is None:
            elapsed_raw = event.get("time_formatted") or event.get("time")
        elapsed_seconds = parse_clock(elapsed_raw)
        period_length = _period_length(period, config)
        remaining_seconds = max(0, period_length - elapsed_seconds)
        raw_id = str(event.get("id") or "%d-%d-%d" % (period, elapsed_seconds, index))
        shot_id = "%s-%s" % (event_type, raw_id)
        if shot_id in seen_ids:
            shot_id = "%s-%d" % (shot_id, index)
        seen_ids.add(shot_id)

        player_id, shooter_number, shooter_name = _player_fields(event)
        player = event.get("player") or event.get("goal_scorer") or {}
        team_id = str(event.get("team_id") or event.get("player_team_id") or player.get("team_id") or "")
        team_code = str(event.get("team_code") or player.get("team_code") or team_id)
        goal_reference = event.get("game_goal_id")
        is_goal = event_type == "goal" or (goal_reference not in (None, "", "0", 0))
        x = optional_float(event.get("x_location"))
        y = optional_float(event.get("y_location"))
        shots.append(
            {
                "shot_id": shot_id,
                "source_event_id": raw_id,
                "game_id": str(config["game_id"]),
                "event_type": event_type,
                "shot_universe": universe,
                "period": period,
                "elapsed_seconds": elapsed_seconds,
                "time_elapsed": format_clock(elapsed_seconds),
                "remaining_seconds": remaining_seconds,
                "time_remaining": format_clock(remaining_seconds),
                "team_id": team_id,
                "team_code": team_code,
                "is_home": str(event.get("home") or "") == "1",
                "shooter_id": player_id,
                "shooter_number": shooter_number,
                "shooter_name": shooter_name,
                "x": "" if x is None else x,
                "y": "" if y is None else y,
                "shot_type": str(event.get("shot_type_description") or ""),
                "is_goal": is_goal,
                "quality": str(event.get("shot_quality_description") or ""),
            }
        )

    shots.sort(key=lambda row: (int(row["period"]), int(row["elapsed_seconds"]), row["shot_id"]))
    coordinate_count = sum(1 for row in shots if row["x"] != "" and row["y"] != "")
    report = {
        "game_id": str(config["game_id"]),
        "shot_universe": universe,
        "event_count": len(events),
        "candidate_count": len(shots),
        "coordinate_count": coordinate_count,
        "coordinate_coverage": (coordinate_count / len(shots)) if shots else 0.0,
        "feed_sha256": sha256_bytes(canonical_json_bytes(payload)),
    }
    return shots, report
