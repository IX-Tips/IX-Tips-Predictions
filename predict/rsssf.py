"""
RSSSF current-season African domestic fixtures adapter.

RSSSF publishes season pages as preformatted text. Only matches with an
explicit calendar date are ingested; undated/TBA rows are intentionally
ignored. The adapter is fixture-only and does not provide bookmaker odds.
"""
import hashlib
import logging
import re
from datetime import date, datetime

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

RSSSF_COMPETITIONS = {
    "ANG": {"directory": "a", "slug": "ango"},
    "BEN": {"directory": "b", "slug": "benin"},
    "BFA": {"directory": "b", "slug": "burkf"},
    "BDI": {"directory": "b", "slug": "buru"},
    "LBR": {"directory": "l", "slug": "liber"},
    "MTN": {"directory": "m", "slug": "maur"},
}

BASE_URL = "https://www.rsssf.org"
CACHE_TTL = 60 * 60 * 4
USER_AGENT = "IX-Tips-Predictions/1.0 (+fixture data attribution: RSSSF)"

_MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
)}
_DATE_RE = re.compile(r"\[(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\]", re.I)
_MATCH_RE = re.compile(
    r"^\s{2,}(?P<home>.+?)\s{2,}"
    r"(?P<score>\d+\s*-\s*\d+|-)\s{2,}(?P<away>.+?)\s*$"
)


def season_year_for_date(value):
    """RSSSF pages use split seasons beginning in the second half of the year."""
    if isinstance(value, str):
        value = datetime.strptime(value[:10], "%Y-%m-%d").date()
    return value.year if value.month >= 7 else value.year - 1


def _page_url(competition_code, season_year):
    config = RSSSF_COMPETITIONS.get(str(competition_code or "").upper())
    if not config:
        return None
    end_year = int(season_year) + 1
    return (
        f"{BASE_URL}/tables{config['directory']}/"
        f"{config['slug']}{end_year}.html"
    )


def _safe_cache_get(key):
    try:
        return cache.get(key)
    except Exception as exc:
        logger.warning("RSSSF cache read failed for %s: %s", key, exc)
        return None


def _safe_cache_set(key, value):
    try:
        cache.set(key, value, timeout=CACHE_TTL)
    except Exception as exc:
        logger.warning("RSSSF cache write failed for %s: %s", key, exc)


def parse_rsssf_text(content, competition_code, season_year):
    """Parse RSSSF round listings into football-data-shaped match objects."""
    code = str(competition_code or "").upper()
    start_year = int(season_year)
    current_day = None
    matches = []
    seen = set()

    for raw_line in (content or "").splitlines():
        date_match = _DATE_RE.search(raw_line)
        if date_match:
            month = _MONTHS[date_match.group(1).title()]
            day = int(date_match.group(2))
            year = start_year if month >= 7 else start_year + 1
            try:
                current_day = date(year, month, day).isoformat()
            except ValueError:
                current_day = None
        elif re.search(r"\[\s*(?:tba|date tba|postponed)\s*\]", raw_line, re.I):
            current_day = None

        if not current_day:
            continue

        match = _MATCH_RE.match(raw_line)
        if not match:
            continue

        home = match.group("home").strip()
        away = re.sub(r"\s+\[.*$", "", match.group("away")).strip()
        score = match.group("score").strip()
        if not home or not away:
            continue

        if score == "-":
            home_goals = away_goals = None
            status = "TIMED"
        else:
            score_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", score)
            if not score_match:
                continue
            home_goals, away_goals = map(int, score_match.groups())
            status = "FINISHED"

        identity = "|".join((code, current_day, home.casefold(), away.casefold()))
        if identity in seen:
            continue
        seen.add(identity)
        match_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
        matches.append({
            "id": f"RSSSF-{code}-{match_id}",
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
    url = _page_url(code, season_year)
    if not url:
        return []

    cache_key = f"rsssf:season:v1:{code}:{int(season_year)}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=20,
        )
        if response.status_code == 404:
            matches = []
        else:
            response.raise_for_status()
            matches = parse_rsssf_text(response.text, code, season_year)
    except Exception as exc:
        logger.warning(
            "RSSSF fixture fetch failed for %s season=%s: %s",
            code, season_year, exc,
        )
        matches = []

    _safe_cache_set(cache_key, matches)
    return matches


def fetch_matches_by_date(competition_code, match_date):
    day = str(match_date)[:10]
    season_year = season_year_for_date(day)
    return [
        match for match in fetch_matches_by_season(competition_code, season_year)
        if (match.get("utcDate") or "")[:10] == day
    ]
