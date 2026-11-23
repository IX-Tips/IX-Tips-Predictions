"""
Official Football Kenya Federation (FKF) fixture adapter.

The FKF website exposes public fixture data through the Supabase project used
by its frontend. Its browser anon key is discovered from the public bundle and
cached; it is not a server-side credential.
"""
import logging
import re
from datetime import date, datetime

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

SITE_URL = "https://footballkenya.org"
SUPABASE_URL = "https://wmcfdzqntemdnrguqijw.supabase.co"
FIXTURE_TABLE = "spl_fixtures"
TEAM_TABLE = "spl_teams"
USER_AGENT = "IX-Tips-Predictions/1.0 (FKF public fixture integration)"
KEY_CACHE = "fkf:supabase:anon-key:v1"
KEY_CACHE_TTL = 60 * 60 * 24
SEASON_CACHE_TTL = 60 * 30
TEAMS_CACHE_TTL = 60 * 60 * 6

_STATUS_MAP = {
    "completed": "FINISHED",
    "finished": "FINISHED",
    "live": "IN_PLAY",
    "in_play": "IN_PLAY",
    "scheduled": "TIMED",
    "upcoming": "TIMED",
    "postponed": "POSTPONED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "abandoned": "CANCELLED",
}


def season_for_date(value):
    """FKF split season; July marks the start of the new season."""
    if isinstance(value, str):
        value = datetime.strptime(value[:10], "%Y-%m-%d").date()
    start_year = value.year if value.month >= 7 else value.year - 1
    return f"{start_year}/{start_year + 1}"


def _public_anon_key():
    cached = cache.get(KEY_CACHE)
    if cached:
        return cached

    page = requests.get(SITE_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    page.raise_for_status()
    scripts = re.findall(
        r"""<script[^>]+src=["']([^"']+\.js(?:\?[^"']*)?)["']""",
        page.text,
        flags=re.IGNORECASE,
    )
    if not scripts:
        raise RuntimeError("FKF website did not expose a JavaScript bundle")

    key_pattern = re.compile(
        r"eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    )
    for script in scripts:
        bundle_url = requests.compat.urljoin(SITE_URL, script)
        response = requests.get(
            bundle_url, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        response.raise_for_status()
        match = key_pattern.search(response.text)
        if match:
            key = match.group(0)
            cache.set(KEY_CACHE, key, timeout=KEY_CACHE_TTL)
            return key

    raise RuntimeError("Could not locate FKF public Supabase anon key")


def _headers():
    key = _public_anon_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "User-Agent": USER_AGENT,
    }


def _rest_rows(table, params):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_headers(),
        params=params,
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"FKF Supabase returned a non-list payload for {table}")
    return payload


def _team_rows():
    cache_key = "fkf:teams:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = _rest_rows(
        TEAM_TABLE,
        {
            "select": "id,team_name,short_name,logo_url,season,played,won,drawn,lost,goals_for,goals_against,goal_difference,points",
            "limit": "1000",
        },
    )
    cache.set(cache_key, rows, timeout=TEAMS_CACHE_TTL)
    return rows


def _season_rows(season):
    cache_key = f"fkf:fixtures:v1:{season}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = _rest_rows(
        FIXTURE_TABLE,
        {
            "select": "id,home_team_id,away_team_id,match_date,match_time,venue,matchday,season,home_score,away_score,status",
            "season": f"eq.{season}",
            "order": "match_date.asc,match_time.asc",
            "limit": "1000",
        },
    )
    cache.set(cache_key, rows, timeout=SEASON_CACHE_TTL)
    return rows


def _parse_kickoff(match_date, match_time):
    day = str(match_date or "").strip()[:10]
    if not day:
        return None
    clock = str(match_time or "").strip()
    if not clock:
        return day
    try:
        parsed = datetime.strptime(clock[:8], "%H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.strptime(clock[:5], "%H:%M")
        except ValueError:
            return day
    # FKF match times are published in Kenya local time (UTC+03:00).
    return f"{day}T{parsed:%H:%M:%S}+03:00"


def _score(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_fixture(row, teams):
    home = teams.get(str(row.get("home_team_id")))
    away = teams.get(str(row.get("away_team_id")))
    if not home or not away:
        return None

    status = _STATUS_MAP.get(str(row.get("status") or "").strip().lower(), "TIMED")
    home_score = _score(row.get("home_score"))
    away_score = _score(row.get("away_score"))
    if status == "FINISHED" and (home_score is None or away_score is None):
        status = "TIMED"

    fixture_id = str(row.get("id") or "").strip()
    kickoff = _parse_kickoff(row.get("match_date"), row.get("match_time"))
    if not fixture_id or not kickoff:
        return None

    return {
        "id": f"FKF-{fixture_id}",
        "utcDate": kickoff,
        "status": status,
        "homeTeam": {
            "name": home.get("team_name"),
            "shortName": home.get("short_name") or home.get("team_name"),
            "crest": home.get("logo_url"),
        },
        "awayTeam": {
            "name": away.get("team_name"),
            "shortName": away.get("short_name") or away.get("team_name"),
            "crest": away.get("logo_url"),
        },
        "score": {
            "fullTime": {
                "home": home_score if status == "FINISHED" else None,
                "away": away_score if status == "FINISHED" else None,
            },
            "halfTime": {"home": None, "away": None},
        },
    }


def _season_matches(season):
    rows = _season_rows(season)
    teams = {
        str(team.get("id")): team
        for team in _team_rows()
        if team.get("id")
    }
    matches = [
        match for match in (normalize_fixture(row, teams) for row in rows)
        if match
    ]
    return sorted(matches, key=lambda item: item["utcDate"])


def fetch_matches_by_season(competition_code, season_year):
    if str(competition_code or "").upper() != "KEN":
        return []
    year = int(season_year)
    return _season_matches(f"{year}/{year + 1}")


def fetch_matches_by_date(competition_code, match_date):
    if str(competition_code or "").upper() != "KEN":
        return []
    day = str(match_date)[:10]
    matches = _season_matches(season_for_date(day))
    return [match for match in matches if match["utcDate"][:10] == day]


def fetch_teams(competition_code):
    if str(competition_code or "").upper() != "KEN":
        return []
    current_season = season_for_date(date.today())
    teams = {}
    for row in _team_rows():
        if row.get("season") != current_season:
            continue
        name = row.get("team_name")
        if name:
            teams[name.casefold()] = {
                "name": name,
                "shortName": row.get("short_name") or name,
                "crest": row.get("logo_url"),
            }
    return sorted(teams.values(), key=lambda item: item["name"].casefold())


def fetch_standings(competition_code):
    if str(competition_code or "").upper() != "KEN":
        return []
    current_season = season_for_date(date.today())
    table = []
    for row in _team_rows():
        if row.get("season") != current_season or not row.get("team_name"):
            continue
        played = _score(row.get("played")) or 0
        won = _score(row.get("won")) or 0
        drawn = _score(row.get("drawn")) or 0
        lost = _score(row.get("lost")) or 0
        goals_for = _score(row.get("goals_for")) or 0
        goals_against = _score(row.get("goals_against")) or 0
        table.append({
            "team": {
                "name": row["team_name"],
                "shortName": row.get("short_name") or row["team_name"],
                "crest": row.get("logo_url"),
                "tla": row.get("short_name"),
            },
            "playedGames": played,
            "won": won,
            "draw": drawn,
            "lost": lost,
            "points": _score(row.get("points")) or 0,
            "goalsFor": goals_for,
            "goalsAgainst": goals_against,
            "goalDifference": _score(row.get("goal_difference")) or 0,
        })
    table.sort(
        key=lambda item: (
            -item["points"], -item["goalDifference"],
            -item["goalsFor"], item["team"]["name"].casefold(),
        )
    )
    for position, item in enumerate(table, start=1):
        item["position"] = position
    return table
