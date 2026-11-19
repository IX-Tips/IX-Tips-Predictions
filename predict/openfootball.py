"""
OpenFootball Africa adapter.

OpenFootball publishes match schedules and results as Football.TXT files in
its world repository. The data is dedicated to the public domain (CC0).
The repository is volunteer-maintained; updates and current-season coverage
are not guaranteed. No API key is required.
"""
import hashlib
import logging
import re
from datetime import date, datetime

import requests
from django.core.cache import cache

from .constants import OPENFOOTBALL_COMPETITIONS

logger = logging.getLogger(__name__)

TREE_URL = "https://api.github.com/repos/openfootball/world/git/trees/master?recursive=1"
RAW_BASE_URL = "https://raw.githubusercontent.com/openfootball/world/master"
TREE_CACHE_KEY = "openfootball:africa-tree:v1"
TREE_CACHE_TTL = 60 * 60 * 12
CURRENT_SEASON_CACHE_TTL = 60 * 60 * 6
HISTORICAL_FILE_CACHE_TTL = 60 * 60 * 24 * 30

_DATE_LINE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}$"
)
_MATCH_LINE = re.compile(r"^\s{2,}(?P<home>.+?)\s{2,}v\s+(?P<tail>.+?)\s*$")
_SCORE_LINE = re.compile(
    r"^(?P<away>.+?)\s+(?P<home_goals>\d+)\s*-\s*(?P<away_goals>\d+)(?:\s+.*)?$"
)


def _competition(code):
    return OPENFOOTBALL_COMPETITIONS.get(str(code or "").upper())


def is_calendar_year_competition(code):
    config = _competition(code)
    return bool(config and config.get("calendar_year"))


def season_year_for_date(code, value):
    if isinstance(value, str):
        value = datetime.strptime(value[:10], "%Y-%m-%d").date()
    if is_calendar_year_competition(code):
        return value.year
    return value.year if value.month >= 7 else value.year - 1


def current_season_year(code, reference_date=None):
    return season_year_for_date(code, reference_date or date.today())


def _safe_cache_get(key):
    try:
        return cache.get(key)
    except Exception as exc:
        logger.warning("OpenFootball cache read failed for %s: %s", key, exc)
        return None


def _safe_cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout=timeout)
    except Exception as exc:
        logger.warning("OpenFootball cache write failed for %s: %s", key, exc)


def _tree_paths():
    cached = _safe_cache_get(TREE_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            TREE_URL,
            headers={"User-Agent": "IX-Tips-Predictions/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        paths = [
            item.get("path")
            for item in payload.get("tree", [])
            if item.get("type") == "blob"
            and str(item.get("path", "")).startswith("africa/")
            and str(item.get("path", "")).endswith(".txt")
        ]
        _safe_cache_set(TREE_CACHE_KEY, paths, TREE_CACHE_TTL)
        return paths
    except Exception as exc:
        logger.warning("Could not read OpenFootball Africa index: %s", exc)
        return []


def _season_file_path(code, season_year):
    config = _competition(code)
    if not config:
        return None
    year = int(season_year)
    if config.get("calendar_year"):
        prefix = f"{year}_"
    else:
        prefix = f"{year}-{str(year + 1)[-2:]}_"
    suffix = f"_{config['suffix']}.txt"
    folder = f"africa/{config['country']}/"
    candidates = [
        path for path in _tree_paths()
        if path.startswith(folder)
        and path.rsplit("/", 1)[-1].startswith(prefix)
        and path.endswith(suffix)
    ]
    return sorted(candidates)[0] if candidates else None


def parse_openfootball_text(content, competition_code, season_year):
    """Parse dated Football.TXT match rows into football-data-shaped objects."""
    matches = []
    current_day = None
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if _DATE_LINE.match(line):
            try:
                current_day = datetime.strptime(line, "%a %b %d %Y").date().isoformat()
            except ValueError:
                current_day = None
            continue

        match_line = _MATCH_LINE.match(raw_line)
        if not match_line or not current_day:
            continue

        home = match_line.group("home").strip()
        tail = match_line.group("tail").strip()
        if not home or not tail:
            continue

        score_match = _SCORE_LINE.match(tail)
        if score_match:
            away = score_match.group("away").strip()
            home_goals = int(score_match.group("home_goals"))
            away_goals = int(score_match.group("away_goals"))
        else:
            away = tail
            home_goals = away_goals = None
        if not away:
            continue

        lowered = tail.casefold()
        if "postpon" in lowered:
            status = "POSTPONED"
            home_goals = away_goals = None
        elif "cancel" in lowered or "abandon" in lowered:
            status = "CANCELLED"
            home_goals = away_goals = None
        else:
            status = "FINISHED" if home_goals is not None and away_goals is not None else "TIMED"

        identity = "|".join((
            str(competition_code), str(season_year), current_day,
            home.casefold(), away.casefold(),
        ))
        match_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
        matches.append({
            "id": f"OF-{competition_code}-{match_id}",
            "utcDate": current_day,
            "status": status,
            "homeTeam": {"name": home, "shortName": home, "crest": None},
            "awayTeam": {"name": away, "shortName": away, "crest": None},
            "score": {
                "fullTime": {"home": home_goals, "away": away_goals},
                "halfTime": {"home": None, "away": None},
            },
        })
    return matches


def fetch_matches_by_season(competition_code, season_year):
    code = str(competition_code or "").upper()
    if not _competition(code):
        return []
    year = int(season_year)
    current_year = current_season_year(code)
    cache_key = f"openfootball:season:v1:{code}:{year}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        return cached

    path = _season_file_path(code, year)
    timeout = CURRENT_SEASON_CACHE_TTL if year >= current_year else HISTORICAL_FILE_CACHE_TTL
    if not path:
        _safe_cache_set(cache_key, [], timeout)
        return []

    try:
        response = requests.get(
            f"{RAW_BASE_URL}/{path}",
            headers={"User-Agent": "IX-Tips-Predictions/1.0"},
            timeout=20,
        )
        if response.status_code == 404:
            matches = []
        else:
            response.raise_for_status()
            matches = parse_openfootball_text(response.text, code, year)
    except Exception as exc:
        logger.warning("OpenFootball fetch failed for %s season=%s: %s", code, year, exc)
        matches = []

    _safe_cache_set(cache_key, matches, timeout)
    return matches


def current_matches(competition_code):
    code = str(competition_code or "").upper()
    return fetch_matches_by_season(code, current_season_year(code))


def fetch_matches_by_date(competition_code, match_date):
    day = str(match_date)[:10]
    year = season_year_for_date(competition_code, day)
    return [
        match for match in fetch_matches_by_season(competition_code, year)
        if (match.get("utcDate") or "")[:10] == day
    ]


def _build_standings(matches):
    table = {}

    def row_for(name):
        return table.setdefault(name, {
            "team": {"name": name, "shortName": name, "crest": None, "tla": None},
            "playedGames": 0, "won": 0, "draw": 0, "lost": 0,
            "points": 0, "goalsFor": 0, "goalsAgainst": 0, "goalDifference": 0,
        })

    for match in matches:
        if match.get("status") != "FINISHED":
            continue
        home = (match.get("homeTeam") or {}).get("name")
        away = (match.get("awayTeam") or {}).get("name")
        score = (match.get("score") or {}).get("fullTime") or {}
        hg, ag = score.get("home"), score.get("away")
        if not home or not away or hg is None or ag is None:
            continue
        h, a = row_for(home), row_for(away)
        h["playedGames"] += 1
        a["playedGames"] += 1
        h["goalsFor"] += hg
        h["goalsAgainst"] += ag
        a["goalsFor"] += ag
        a["goalsAgainst"] += hg
        if hg > ag:
            h["won"] += 1
            h["points"] += 3
            a["lost"] += 1
        elif hg < ag:
            a["won"] += 1
            a["points"] += 3
            h["lost"] += 1
        else:
            h["draw"] += 1
            a["draw"] += 1
            h["points"] += 1
            a["points"] += 1

    rows = list(table.values())
    for item in rows:
        item["goalDifference"] = item["goalsFor"] - item["goalsAgainst"]
    rows.sort(
        key=lambda item: (
            -item["points"], -item["goalDifference"], -item["goalsFor"],
            item["team"]["name"].casefold(),
        )
    )
    for position, item in enumerate(rows, start=1):
        item["position"] = position
    return rows


def fetch_standings(competition_code):
    return _build_standings(current_matches(competition_code))


def _latest_available_season_year(competition_code):
    config = _competition(competition_code)
    if not config:
        return None
    folder = f"africa/{config['country']}/"
    suffix = f"_{config['suffix']}.txt"
    years = []
    for path in _tree_paths():
        if not path.startswith(folder) or not path.endswith(suffix):
            continue
        filename = path.rsplit("/", 1)[-1]
        match = re.match(r"^(\d{4})(?:-\d{2,4})?_", filename)
        if match:
            years.append(int(match.group(1)))
    return max(years) if years else None


def fetch_teams(competition_code):
    year = _latest_available_season_year(competition_code)
    if year is None:
        return []
    matches = fetch_matches_by_season(competition_code, year)
    names = {
        (match.get("homeTeam") or {}).get("name")
        for match in matches
    } | {
        (match.get("awayTeam") or {}).get("name")
        for match in matches
    }
    return [
        {"name": name, "shortName": name, "crest": None}
        for name in sorted(name for name in names if name)
    ]


def fetch_scorers(competition_code):
    return []
