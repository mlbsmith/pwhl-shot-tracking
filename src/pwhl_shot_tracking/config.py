import copy
from pathlib import Path
from typing import Any, Dict

from .utils import read_json, write_json


DEFAULT_CONFIG = {
    "shot_universe": "shots_on_goal",
    "work_dir": "work/game",
    "hockeytech": {
        "base_url": "https://lscluster.hockeytech.com/feed/index.php",
        "client_code": "pwhl",
        "feed_key": "446521baf8c38984",
        "timeout_seconds": 30,
    },
    "gemini": {
        "model": "gemini-3.1-pro-preview",
        "api_surface": "generateContent",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "fps": 8.0,
        "media_resolution": "MEDIA_RESOLUTION_HIGH",
        "timeout_seconds": 180,
        "max_attempts": 4,
        "max_api_calls_per_run": 120,
    },
    "clip": {"seconds_before": 12.0, "seconds_after": 2.0},
    "clock": {
        "regulation_period_seconds": 1200,
        "overtime_period_seconds": 1200,
        "max_extrapolation_seconds": 2.0,
        "max_stoppage_gap_seconds": 3.0,
    },
    "validation": {"minimum_precision": 0.85, "minimum_recall": 0.80},
    "team_colors": {"DEFAULT_HOME": "#6D2077", "DEFAULT_AWAY": "#1B365D"},
}

SHOT_UNIVERSES = {"shots_on_goal", "fenwick", "all_attempts"}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path) -> Dict[str, Any]:
    path = path.resolve()
    config = _deep_merge(DEFAULT_CONFIG, read_json(path))
    if not str(config.get("game_id", "")).strip():
        raise ValueError("config.game_id is required")
    if not str(config.get("video_url", "")).startswith(("https://www.youtube.com/", "https://youtu.be/")):
        raise ValueError("config.video_url must be a public YouTube URL")
    if config["shot_universe"] not in SHOT_UNIVERSES:
        raise ValueError("config.shot_universe must be one of: %s" % ", ".join(sorted(SHOT_UNIVERSES)))
    work_dir = Path(config["work_dir"])
    if not work_dir.is_absolute():
        work_dir = path.parent / work_dir
    config["work_dir"] = str(work_dir.resolve())
    config["_config_path"] = str(path)
    return config


def create_config(
    path: Path,
    game_id: str,
    video_url: str,
    shot_universe: str,
    game_resolution: Any = None,
) -> Dict[str, Any]:
    if shot_universe not in SHOT_UNIVERSES:
        raise ValueError("unknown shot universe: %s" % shot_universe)
    config = _deep_merge(
        DEFAULT_CONFIG,
        {
            "game_id": str(game_id),
            "video_url": video_url,
            "shot_universe": shot_universe,
            "work_dir": "work/%s" % game_id,
        },
    )
    if game_resolution is not None:
        config["game_resolution"] = game_resolution
    write_json(path, config)
    return config


def work_path(config: Dict[str, Any], *parts: str) -> Path:
    return Path(config["work_dir"]).joinpath(*parts)
