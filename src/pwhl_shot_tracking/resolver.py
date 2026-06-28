import html
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
MONTH_PATTERN = (
    r"january|february|march|april|may|june|july|august|"
    r"september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)


class GameResolutionError(RuntimeError):
    def __init__(self, message: str, report: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.report = report


def extract_youtube_video_id(video_url: str) -> str:
    parsed = urlparse(str(video_url).strip())
    host = (parsed.hostname or "").lower()
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"live", "embed", "shorts"}:
                video_id = parts[1]
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("video_url does not contain a valid YouTube video ID")
    return video_id


def _request_json(url: str, timeout: float, opener: Callable[..., Any]) -> Dict[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "pwhl-shot-tracking/0.1"},
    )
    with opener(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_text(url: str, timeout: float, opener: Callable[..., Any]) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (compatible; pwhl-shot-tracking/0.1)",
        },
    )
    with opener(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _first_json_string(page: str, key: str) -> str:
    match = re.search(r'"%s":"((?:\\.|[^"\\])*)"' % re.escape(key), page)
    if not match:
        return ""
    try:
        return str(json.loads('"%s"' % match.group(1)))
    except json.JSONDecodeError:
        return html.unescape(match.group(1))


def _iso_date(value: str) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def dates_in_title(title: str) -> List[date]:
    candidates: List[date] = []

    def add(value: Optional[date]) -> None:
        if value is not None and value not in candidates:
            candidates.append(value)

    for match in re.finditer(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", title):
        try:
            add(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            pass
    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", title):
        try:
            add(date(int(match.group(3)), int(match.group(1)), int(match.group(2))))
        except ValueError:
            pass

    month_first = re.compile(
        r"\b(%s)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(20\d{2})\b" % MONTH_PATTERN,
        re.IGNORECASE,
    )
    day_first = re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(%s)\.?(?:,)?\s+(20\d{2})\b" % MONTH_PATTERN,
        re.IGNORECASE,
    )
    for pattern, order in ((month_first, "month_first"), (day_first, "day_first")):
        for match in pattern.finditer(title):
            if order == "month_first":
                value = "%s %s %s" % (match.group(1), match.group(2), match.group(3))
            else:
                value = "%s %s %s" % (match.group(2), match.group(1), match.group(3))
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    add(datetime.strptime(value.replace(".", ""), fmt).date())
                    break
                except ValueError:
                    continue
    return candidates


def fetch_youtube_metadata(
    video_url: str,
    timeout: float = 30.0,
    opener: Callable[..., Any] = urlopen,
) -> Dict[str, Any]:
    video_id = extract_youtube_video_id(video_url)
    canonical_url = "https://www.youtube.com/watch?v=%s" % video_id
    oembed_url = "https://www.youtube.com/oembed?" + urlencode(
        {"url": canonical_url, "format": "json"}
    )
    oembed = _request_json(oembed_url, timeout, opener)
    page = _request_text(canonical_url, timeout, opener)
    title = str(oembed.get("title") or _first_json_string(page, "title") or "").strip()
    if not title:
        raise GameResolutionError("YouTube returned no video title; verify that the video is public")
    publish_text = _first_json_string(page, "publishDate")
    upload_text = _first_json_string(page, "uploadDate")
    duration_text = _first_json_string(page, "lengthSeconds")
    try:
        duration_seconds = int(duration_text) if duration_text else None
    except ValueError:
        duration_seconds = None
    return {
        "video_id": video_id,
        "canonical_url": canonical_url,
        "title": title,
        "author_name": str(oembed.get("author_name") or _first_json_string(page, "ownerChannelName") or ""),
        "publish_date": _iso_date(publish_text).isoformat() if _iso_date(publish_text) else None,
        "upload_date": _iso_date(upload_text).isoformat() if _iso_date(upload_text) else None,
        "duration_seconds": duration_seconds,
        "title_dates": [value.isoformat() for value in dates_in_title(title)],
    }


def _hockeytech_json(
    settings: Dict[str, Any],
    parameters: Dict[str, Any],
    opener: Callable[..., Any],
) -> Dict[str, Any]:
    query = {
        **parameters,
        "key": settings["feed_key"],
        "client_code": settings["client_code"],
    }
    url = str(settings["base_url"]) + "?" + urlencode(query)
    return _request_json(url, float(settings["timeout_seconds"]), opener)


def fetch_seasons(
    settings: Dict[str, Any],
    opener: Callable[..., Any] = urlopen,
) -> List[Dict[str, Any]]:
    payload = _hockeytech_json(settings, {"feed": "modulekit", "view": "seasons"}, opener)
    seasons = payload.get("SiteKit", {}).get("Seasons")
    if not isinstance(seasons, list):
        raise GameResolutionError("HockeyTech season response has no SiteKit.Seasons list")
    return seasons


def fetch_schedule(
    settings: Dict[str, Any],
    season_id: str,
    opener: Callable[..., Any] = urlopen,
) -> List[Dict[str, Any]]:
    payload = _hockeytech_json(
        settings,
        {"feed": "modulekit", "view": "schedule", "season_id": str(season_id)},
        opener,
    )
    schedule = payload.get("SiteKit", {}).get("Schedule")
    if schedule is None:
        return []
    if not isinstance(schedule, list):
        raise GameResolutionError("HockeyTech schedule response has no SiteKit.Schedule list")
    return schedule


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _team_aliases(game: Dict[str, Any], prefix: str) -> List[Tuple[str, int, str]]:
    aliases = []
    fields = [
        ("%s_team_name" % prefix, 6),
        ("%s_team_city" % prefix, 5),
        ("%s_team_nickname" % prefix, 5),
        ("%s_team_code" % prefix, 4),
    ]
    seen = set()
    for field, weight in fields:
        original = str(game.get(field) or "").strip()
        normalized = _normalized(original)
        if len(normalized) >= 3 and normalized not in seen:
            seen.add(normalized)
            aliases.append((normalized, weight, original))
    return aliases


def _best_alias_match(title: str, aliases: Sequence[Tuple[str, int, str]]) -> Optional[Dict[str, Any]]:
    padded = " " + title + " "
    matches = [
        {"alias": original, "normalized_alias": normalized, "weight": weight}
        for normalized, weight, original in aliases
        if (" " + normalized + " ") in padded
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (int(item["weight"]), len(str(item["normalized_alias"]))))


def _game_date(game: Dict[str, Any]) -> Optional[date]:
    return _iso_date(str(game.get("date_played") or game.get("GameDateISO8601") or ""))


def _reference_dates(metadata: Dict[str, Any]) -> Tuple[List[date], Optional[date]]:
    explicit = [
        parsed
        for parsed in (_iso_date(str(value)) for value in metadata.get("title_dates", []))
        if parsed is not None
    ]
    published = _iso_date(str(metadata.get("publish_date") or metadata.get("upload_date") or ""))
    return explicit, published


def score_game(metadata: Dict[str, Any], game: Dict[str, Any]) -> Dict[str, Any]:
    title = _normalized(metadata.get("title"))
    home_match = _best_alias_match(title, _team_aliases(game, "home"))
    visiting_match = _best_alias_match(title, _team_aliases(game, "visiting"))
    both_teams = home_match is not None and visiting_match is not None
    score = 0
    reasons = []
    if both_teams:
        score += 70
        reasons.append("both teams appear in title")
    elif home_match or visiting_match:
        score += 18
        reasons.append("only one team appears in title")
    if home_match:
        score += int(home_match["weight"])
    if visiting_match:
        score += int(visiting_match["weight"])

    scheduled = _game_date(game)
    explicit_dates, published = _reference_dates(metadata)
    explicit_delta = None
    publish_delta = None
    if scheduled is not None and explicit_dates:
        explicit_delta = min(abs((scheduled - candidate).days) for candidate in explicit_dates)
        if explicit_delta == 0:
            score += 35
            reasons.append("title date matches schedule")
        elif explicit_delta == 1:
            score += 12
            reasons.append("title date is one day from schedule")
        else:
            score -= 20
            reasons.append("title date conflicts with schedule")
    if scheduled is not None and published is not None:
        publish_delta = abs((scheduled - published).days)
        if publish_delta == 0:
            score += 25
            reasons.append("publish date matches schedule")
        elif publish_delta == 1:
            score += 18
            reasons.append("publish date is one day from schedule")
        elif publish_delta == 2:
            score += 10
            reasons.append("publish date is two days from schedule")
        elif publish_delta <= 7:
            score += 2
        else:
            score -= min(15, publish_delta)

    return {
        "game_id": str(game.get("game_id") or game.get("id") or ""),
        "season_id": str(game.get("season_id") or ""),
        "date_played": scheduled.isoformat() if scheduled else None,
        "home_team": str(game.get("home_team_name") or game.get("home_team_code") or ""),
        "visiting_team": str(game.get("visiting_team_name") or game.get("visiting_team_code") or ""),
        "home_team_code": str(game.get("home_team_code") or ""),
        "visiting_team_code": str(game.get("visiting_team_code") or ""),
        "score": score,
        "both_teams_matched": both_teams,
        "home_title_match": home_match,
        "visiting_title_match": visiting_match,
        "title_date_delta_days": explicit_delta,
        "publish_date_delta_days": publish_delta,
        "reasons": reasons,
    }


def rank_games(metadata: Dict[str, Any], games: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        (score_game(metadata, game) for game in games),
        key=lambda item: (-int(item["score"]), str(item.get("date_played") or ""), str(item["game_id"])),
    )


def select_game(
    metadata: Dict[str, Any],
    games: Iterable[Dict[str, Any]],
    minimum_score: int = 95,
    minimum_margin: int = 10,
) -> Dict[str, Any]:
    ranked = rank_games(metadata, games)
    top = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    margin = int(top["score"]) - int(runner_up["score"]) if top and runner_up else int(top["score"]) if top else 0
    resolved = bool(
        top
        and top["game_id"]
        and top["both_teams_matched"]
        and int(top["score"]) >= minimum_score
        and margin >= minimum_margin
    )
    return {
        "status": "resolved" if resolved else "ambiguous",
        "game_id": top["game_id"] if resolved and top else None,
        "minimum_score": minimum_score,
        "minimum_margin": minimum_margin,
        "top_score_margin": margin,
        "selected": top if resolved else None,
        "candidates": ranked[:10],
    }


def _candidate_seasons(seasons: Iterable[Dict[str, Any]], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit, published = _reference_dates(metadata)
    reference = explicit[0] if explicit else published
    materialized = list(seasons)
    if reference is None:
        return materialized
    nearby = []
    for season in materialized:
        start = _iso_date(str(season.get("start_date") or ""))
        end = _iso_date(str(season.get("end_date") or ""))
        if start and end and (start - timedelta(days=14)) <= reference <= (end + timedelta(days=14)):
            nearby.append(season)
    return nearby or materialized


def resolve_game_id(
    video_url: str,
    hockeytech_settings: Dict[str, Any],
    opener: Callable[..., Any] = urlopen,
) -> Dict[str, Any]:
    metadata = fetch_youtube_metadata(
        video_url,
        timeout=float(hockeytech_settings.get("timeout_seconds", 30)),
        opener=opener,
    )
    seasons = fetch_seasons(hockeytech_settings, opener=opener)
    searched_seasons = _candidate_seasons(seasons, metadata)
    games = []
    for season in searched_seasons:
        games.extend(fetch_schedule(hockeytech_settings, str(season["season_id"]), opener=opener))
    selection = select_game(metadata, games)
    if selection["status"] != "resolved" and len(searched_seasons) < len(seasons):
        searched_ids = {str(item.get("season_id") or "") for item in searched_seasons}
        remaining = [item for item in seasons if str(item.get("season_id") or "") not in searched_ids]
        for season in remaining:
            games.extend(fetch_schedule(hockeytech_settings, str(season["season_id"]), opener=opener))
        searched_seasons = searched_seasons + remaining
        selection = select_game(metadata, games)
    warnings = []
    duration = metadata.get("duration_seconds")
    if isinstance(duration, int) and duration < 3600:
        warnings.append("video is shorter than one hour and may not be a full-game broadcast")
    report = {
        "strategy": "youtube-title-date-to-hockeytech-schedule-v1",
        "video": metadata,
        "searched_seasons": [
            {"season_id": str(item.get("season_id") or ""), "season_name": str(item.get("season_name") or "")}
            for item in searched_seasons
        ],
        "warnings": warnings,
        **selection,
    }
    if selection["status"] != "resolved":
        raise GameResolutionError(
            "could not resolve one unambiguous HockeyTech game from the YouTube title/date",
            report,
        )
    return report
