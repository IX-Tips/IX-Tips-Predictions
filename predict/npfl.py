"""
Public fixture adapter for the Nigeria Professional Football League.

The NPFL fixtures page is checked against robots.txt before every uncached
fetch. The HTML is parsed conservatively: only rows with a valid kickoff,
two team names, and a recognizable match separator are accepted.
"""
import hashlib
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.robotparser import RobotFileParser

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

FIXTURES_URL = "https://npfl.com.ng/fixtures-results/"
ROBOTS_URL = "https://npfl.com.ng/robots.txt"
USER_AGENT = "IXTipsSourceAudit/1.0 (+fixture-source diagnostic)"
CACHE_KEY = "npfl:fixtures:v1"
CACHE_TTL = 60 * 30
ROBOTS_CACHE_KEY = "npfl:robots:v1"
ROBOTS_CACHE_TTL = 60 * 10


class _FixtureTableParser(HTMLParser):
    """Extract Sportspress event rows without depending on a DOM package."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None
        self._cell_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "sp-row" in attrs.get("class", "").split():
            self._row = {}
        elif self._row is not None and tag == "td":
            classes = attrs.get("class", "").split()
            self._cell = next((name for name in (
                "data-date", "data-event", "data-time"
            ) if name in classes), None)
            self._cell_text = []
            if self._cell == "data-date":
                self._row["kickoff"] = attrs.get("content")

    def handle_data(self, data):
        if self._row is not None and self._cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._row is not None and self._cell:
            self._row[self._cell] = " ".join(" ".join(self._cell_text).split())
            self._cell = None
            self._cell_text = []
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None
            self._cell_text = []


def _robots_allow_fetch():
    cached = cache.get(ROBOTS_CACHE_KEY)
    if cached is not None:
        return bool(cached)
    parser = RobotFileParser()
    parser.set_url(ROBOTS_URL)
    response = requests.get(
        ROBOTS_URL, headers={"User-Agent": USER_AGENT}, timeout=15
    )
    if response.status_code != 200:
        logger.warning("NPFL robots.txt returned HTTP %s; refusing fixture fetch", response.status_code)
        cache.set(ROBOTS_CACHE_KEY, False, timeout=ROBOTS_CACHE_TTL)
        return False
    parser.parse(response.text.splitlines())
    allowed = parser.can_fetch(USER_AGENT, FIXTURES_URL)
    cache.set(ROBOTS_CACHE_KEY, allowed, timeout=ROBOTS_CACHE_TTL)
    return allowed


def parse_fixture_page(html):
    """Return normalized football-data-shaped fixtures from the NPFL table."""
    parser = _FixtureTableParser()
    parser.feed(html or "")
    matches = []
    for row in parser.rows:
        kickoff = (row.get("kickoff") or "").strip()
        event = (row.get("data-event") or "").strip()
        time_result = (row.get("data-time") or "").strip()
        if not kickoff or not event:
            continue
        try:
            parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        except ValueError:
            continue
        # Sportspress event cells render both club names separated by "vs".
        teams = re.split(r"\\s+v(?:s\\.?)?\\s+", event, maxsplit=1, flags=re.IGNORECASE)
        if len(teams) != 2:
            continue
        home, away = (re.sub(r"\\s+", " ", name).strip(" -–") for name in teams)
        if not home or not away:
            continue

        score = re.search(r"(?<!\\d)(\\d{1,2})\\s*[-–]\\s*(\\d{1,2})(?!\\d)", time_result)
        lowered = time_result.casefold()
        if "postpon" in lowered:
            status, home_goals, away_goals = "POSTPONED", None, None
        elif "cancel" in lowered or "abandon" in lowered:
            status, home_goals, away_goals = "CANCELLED", None, None
        elif score:
            status = "FINISHED"
            home_goals, away_goals = int(score.group(1)), int(score.group(2))
        else:
            status, home_goals, away_goals = "TIMED", None, None

        identity = "|".join((parsed.isoformat(), home.casefold(), away.casefold()))
        stable_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
        matches.append({
            "id": f"NPFL-{stable_id}",
            # Keep the source's explicit +01:00 offset; downstream code can
            # convert it to UTC without losing the original kickoff instant.
            "utcDate": kickoff,
            "status": status,
            "homeTeam": {"name": home, "shortName": home, "crest": None},
            "awayTeam": {"name": away, "shortName": away, "crest": None},
            "score": {
                "fullTime": {"home": home_goals, "away": away_goals},
                "halfTime": {"home": None, "away": None},
            },
        })

    # Keep the better (finished) row if the source repeats a fixture.
    unique = {}
    for match in matches:
        key = (
            match["utcDate"][:10],
            match["homeTeam"]["name"].casefold(),
            match["awayTeam"]["name"].casefold(),
        )
        previous = unique.get(key)
        if previous is None or (
            match["status"] == "FINISHED" and previous["status"] != "FINISHED"
        ):
            unique[key] = match
    return sorted(
        unique.values(),
        key=lambda item: (item["utcDate"], item["homeTeam"]["name"].casefold()),
    )


def current_matches(competition_code="NGA", force_refresh=False):
    if str(competition_code or "").upper() != "NGA":
        return []
    if not force_refresh:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    if not _robots_allow_fetch():
        return []
    response = requests.get(
        FIXTURES_URL, headers={"User-Agent": USER_AGENT}, timeout=20
    )
    response.raise_for_status()
    matches = parse_fixture_page(response.text)
    cache.set(CACHE_KEY, matches, timeout=CACHE_TTL)
    return matches


def fetch_matches_by_date(competition_code, match_date):
    day = str(match_date)[:10]
    return [
        match for match in current_matches(competition_code)
        if (match.get("utcDate") or "")[:10] == day
    ]


def fetch_matches_by_season(competition_code, season_year):
    year = str(int(season_year))
    return [
        match for match in current_matches(competition_code)
        if (match.get("utcDate") or "")[:4] == year
        and match.get("status") == "FINISHED"
    ]
