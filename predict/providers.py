"""
Data-provider abstraction.

Existing leagues are served by football-data.org ("FD"). To cover leagues that
football-data's plan doesn't include, additional leagues can be served by
API-Football ("AF", https://dashboard.api-football.com). This module fetches
from API-Football and normalises every response into the *football-data.org
shape* that the rest of the codebase already expects, so no downstream code
(ML models, views, mobile API) has to change.

Routing is decided per competition by COMPETITION_PROVIDERS in constants.py.
If APIFOOTBALL_KEY is not set, all AF calls return empty gracefully — so adding
AF leagues to the config is harmless until the key is configured.
"""
import hashlib
import logging
import os

import requests
from django.core.cache import cache
from django.utils import timezone

from .constants import (
    API_TOKEN,
    APIFOOTBALL_API_KEY,
    BASE_URL,
    APIFOOTBALL_CALENDAR_YEAR,
    APIFOOTBALL_LEAGUE_IDS,
    APIFOOTBALL_LEAGUE_SEARCH,
    COMPETITION_PROVIDERS,
    FOOTBALL_DATA_UK_CODES,
    LIVEFOOTBALL_LEAGUE_IDS,
    OPEN_MATCHDAY_COMPETITIONS,
    OPENFOOTBALL_COMPETITIONS,
)

logger = logging.getLogger(__name__)

AF_BASE_URL = os.environ.get("APIFOOTBALL_BASE_URL", "https://v3.football.api-sports.io")


def _configured_api_football_key():
    """Support the documented Django setting/env name and the legacy env alias."""
    return os.environ.get("APIFOOTBALL_KEY") or APIFOOTBALL_API_KEY


AF_KEY = _configured_api_football_key()

# Live-Football-Data (FotMob) on RapidAPI
LF_HOST = os.environ.get("LIVEFOOTBALL_HOST", "free-api-live-football-data.p.rapidapi.com")
LF_KEY = os.environ.get("LIVEFOOTBALL_KEY", "")
LF_SEASON_CACHE_TIMEOUT = 60 * 60 * 25  # one daily refresh; reads hit cache

# open-matchday is a public, daily CSV compilation. Its stated terms permit
# informational/non-commercial projects with attribution only.
OM_BASE_URL = os.environ.get(
    "OPEN_MATCHDAY_BASE_URL",
    "https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data",
).rstrip("/")
OM_ENABLED = os.environ.get("OPEN_MATCHDAY_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
OM_CACHE_TIMEOUT = 60 * 60 * 8
OM_FIXTURE_CACHE_TIMEOUT = 60 * 60 * 4
OF_ENABLED = os.environ.get("OPENFOOTBALL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

# API-Football fixture status short codes -> football-data status strings
_AF_FINISHED = {"FT", "AET", "PEN", "AWD", "WO"}
_AF_LIVE = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
_AF_POSTPONED = {"PST"}
_AF_CANCELLED = {"CANC", "ABD"}


def get_provider(competition_code):
    return COMPETITION_PROVIDERS.get(competition_code, "FD")


def is_af(competition_code):
    return get_provider(competition_code) == "AF"


def is_lf(competition_code):
    return get_provider(competition_code) == "LF"


def is_uk(competition_code):
    return get_provider(competition_code) == "UK"


def is_of(competition_code):
    return get_provider(competition_code) == "OF"


def is_fd(competition_code):
    return get_provider(competition_code) == "FD"


# ── Provider dispatch ─────────────────────────────────────────────────────────
# Single dispatch table that all fetcher functions delegate to. When adding a
# new provider, register it here and every fetch function picks it up — no need
# to edit 6+ separate if/elif chains.

_PROVIDER_DISPATCH = {
    "FD": {},
    "AF": {},
    "LF": {},
    "UK": {},
    "OM": {},
    "OF": {},
}


def _register(providers=None, *, operation):
    """
    Decorator that registers a function in the dispatch table for one or more
    providers. Example:

        @_register(providers=["LF","UK"], operation="fetch_matches_by_season")
        def lf_fetch_matches_by_season(code, season): ...

        @_register(providers=["FD"], operation="fetch_matches_by_date")
        def fd_fetch_matches_by_date(code, match_date): ...
    """
    def decorator(fn):
        for p in (providers or []):
            _PROVIDER_DISPATCH[p][operation] = fn
        return fn
    return decorator


def dispatch_provider(competition_code, operation, *args, **kwargs):
    """
    Call the registered handler for `competition_code` and `operation`, falling
    back to FD. Raises KeyError if no handler is registered for either.
    """
    provider = get_provider(competition_code)
    fn = _PROVIDER_DISPATCH.get(provider, {}).get(operation)
    if fn is None:
        fn = _PROVIDER_DISPATCH.get("FD", {}).get(operation)
    if fn is None:
        raise KeyError(f"No provider registered for operation '{operation}' (provider={provider})")
    return fn(competition_code, *args, **kwargs)


# ── Low-level request ─────────────────────────────────────────────────────────

def _record_af_request_health(state):
    """Best-effort API-Football health state; diagnostics never break ingestion."""
    try:
        cache.set(f"provider_request_health::AF::{state.get('endpoint', 'unknown')}", state, timeout=60 * 60 * 24)
    except Exception as exc:
        logger.warning("Unable to cache API-Football diagnostics: %s", exc)


def _af_get(path, params=None):
    """Call API-Football and return the `response` list (handles paging)."""
    if not AF_KEY:
        logger.info("API-Football key not set; skipping API-Football call %s", path)
        _record_af_request_health({
            "status": "not_configured",
            "last_error": "API-Football key is not configured",
            "checked_at": timezone.now().isoformat(),
            "endpoint": path,
        })
        return []

    headers = {"x-apisports-key": AF_KEY}
    params = dict(params or {})
    results = []
    page = 1
    request_error = None
    while True:
        params["page"] = page
        try:
            r = requests.get(f"{AF_BASE_URL}/{path}", headers=headers, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                raise ValueError("API-Football returned an invalid response payload")
        except Exception as exc:
            request_error = str(exc)[:500]
            logger.warning("API-Football request failed: %s params=%s error=%s", path, params, exc)
            break

        if data.get("errors"):
            request_error = str(data["errors"])[:500]
            logger.warning("API-Football returned errors for %s: %s", path, data["errors"])
            break

        results.extend(data.get("response", []) or [])
        paging = data.get("paging", {}) or {}
        if page >= (paging.get("total") or 1):
            break
        page += 1

    _record_af_request_health({
        "status": "error" if request_error else "ok",
        "last_error": request_error,
        "checked_at": timezone.now().isoformat(),
        "endpoint": path,
    })
    return results


# ── Season helpers ────────────────────────────────────────────────────────────

def _af_season_for_date(d):
    """
    API-Football season = the starting year. Split-year leagues (Aug–May) use
    the start year; calendar-year leagues use the calendar year.
    """
    return d.year


def _af_season_for_split_year(d):
    return d.year if d.month >= 7 else d.year - 1


def af_season(competition_code, d):
    if competition_code in APIFOOTBALL_CALENDAR_YEAR:
        return _af_season_for_date(d)
    return _af_season_for_split_year(d)


def af_league_id(competition_code, season_year=None):
    """Resolve a v3 league ID, preferring an explicit account-verified override."""
    code = str(competition_code or "").upper()
    configured_id = APIFOOTBALL_LEAGUE_IDS.get(code)
    if configured_id:
        return configured_id

    lookup = APIFOOTBALL_LEAGUE_SEARCH.get(code)
    if not lookup:
        return None

    country, league_name = lookup
    cache_key = f"af_league_id_v3::{code}::{season_year or 'current'}"
    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached or None
    except Exception:
        pass

    params = {"search": league_name, "country": country}
    if season_year:
        params["season"] = int(season_year)
    rows = _af_get("leagues", params)
    expected_country = country.casefold()
    expected_name = " ".join(league_name.casefold().split())
    league_id = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        league = row.get("league", {}) or {}
        country_data = row.get("country", {}) or {}
        if not isinstance(league, dict) or not isinstance(country_data, dict):
            continue
        actual_country = str(country_data.get("name") or "").strip().casefold()
        actual_name = " ".join(str(league.get("name") or "").strip().casefold().split())
        candidate_id = league.get("id")
        # Provider search results can contain malformed entries. Ignore them
        # rather than failing the entire competition's fixture fallback.
        try:
            candidate_id = int(candidate_id)
        except (TypeError, ValueError):
            continue
        if candidate_id > 0 and actual_country == expected_country and actual_name == expected_name:
            league_id = candidate_id
            break

    try:
        cache.set(cache_key, league_id or 0, timeout=(60 * 60 * 24 if league_id else 60 * 60))
    except Exception:
        pass
    if not league_id:
        logger.info(
            "API-Football v3 league lookup found no exact match for %s (%s, %s, season %s)",
            code, country, league_name, season_year,
        )
    return league_id


# ── Normalisation (API-Football -> football-data shape) ───────────────────────

def _af_status(short):
    if short in _AF_FINISHED:
        return "FINISHED"
    if short in _AF_LIVE:
        return "IN_PLAY"
    if short in _AF_POSTPONED:
        return "POSTPONED"
    if short in _AF_CANCELLED:
        return "CANCELLED"
    return "TIMED"


def _normalize_fixture(fx):
    fixture = fx.get("fixture", {}) or {}
    teams = fx.get("teams", {}) or {}
    goals = fx.get("goals", {}) or {}
    score = fx.get("score", {}) or {}
    ht = score.get("halftime", {}) or {}
    ft = score.get("fulltime", {}) or {}
    home = teams.get("home", {}) or {}
    away = teams.get("away", {}) or {}
    return {
        "id": fixture.get("id"),
        "utcDate": fixture.get("date"),  # ISO 8601 with offset; fromisoformat-safe
        "status": _af_status((fixture.get("status", {}) or {}).get("short")),
        "homeTeam": {"name": home.get("name"), "crest": home.get("logo")},
        "awayTeam": {"name": away.get("name"), "crest": away.get("logo")},
        "score": {
            "fullTime": {
                "home": ft.get("home") if ft.get("home") is not None else goals.get("home"),
                "away": ft.get("away") if ft.get("away") is not None else goals.get("away"),
            },
            "halfTime": {"home": ht.get("home"), "away": ht.get("away")},
        },
    }


# ── Public fetch functions (mirror utils/views football-data fetchers) ────────

def _af_fetch_season_matches(competition_code, season_year):
    """Fetch and cache the season once; date scans then avoid one API call/day."""
    league_id = af_league_id(competition_code, season_year)
    if not league_id:
        return []
    cache_key = f"af_season_fixtures::{competition_code}::{season_year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = _af_get("fixtures", {"league": league_id, "season": season_year})
    matches = [_normalize_fixture(fx) for fx in rows]
    # Refresh several times per day so newly announced fixtures appear quickly.
    cache.set(cache_key, matches, timeout=60 * 60 * 6)
    return matches


@_register(providers=["AF"], operation="fetch_matches_by_date")
def af_fetch_matches_by_date(competition_code, match_date):
    """match_date: 'YYYY-MM-DD'. Returns football-data-shaped match dicts."""
    from datetime import datetime

    d = datetime.strptime(str(match_date)[:10], "%Y-%m-%d").date()
    matches = _af_fetch_season_matches(competition_code, af_season(competition_code, d))
    return [m for m in matches if (m.get("utcDate") or "")[:10] == str(match_date)[:10]]


@_register(providers=["AF"], operation="fetch_matches_by_season")
def af_fetch_matches_by_season(competition_code, season_year):
    return _af_fetch_season_matches(competition_code, season_year)


def af_fetch_scorers(competition_code):
    """Returns football-data-shaped scorer dicts."""
    from datetime import date

    league_id = APIFOOTBALL_LEAGUE_IDS.get(competition_code)
    if not league_id:
        return []
    season = af_season(competition_code, date.today())
    rows = _af_get("players/topscorers", {"league": league_id, "season": season})
    scorers = []
    for row in rows:
        player = row.get("player", {}) or {}
        stats = (row.get("statistics") or [{}])[0]
        team = stats.get("team", {}) or {}
        goals = stats.get("goals", {}) or {}
        penalty = stats.get("penalty", {}) or {}
        scorers.append({
            "player": {"name": player.get("name")},
            "team": {"name": team.get("name")},
            "goals": goals.get("total") or 0,
            "assists": goals.get("assists") or 0,
            "penalties": penalty.get("scored") or 0,
        })
    return scorers


def af_fetch_standings(competition_code):
    """Returns football-data-shaped standings table rows."""
    from datetime import date

    league_id = APIFOOTBALL_LEAGUE_IDS.get(competition_code)
    if not league_id:
        return []
    season = af_season(competition_code, date.today())
    rows = _af_get("standings", {"league": league_id, "season": season})
    if not rows:
        return []
    try:
        groups = rows[0]["league"]["standings"]  # list of groups
        flat = groups[0] if groups else []
    except (KeyError, IndexError, TypeError):
        return []

    table = []
    for r in flat:
        team = r.get("team", {}) or {}
        all_stats = r.get("all", {}) or {}
        goals = all_stats.get("goals", {}) or {}
        table.append({
            "position": r.get("rank"),
            "team": {
                "name": team.get("name"),
                "shortName": team.get("name"),
                "crest": team.get("logo"),
                "tla": None,
            },
            "playedGames": all_stats.get("played"),
            "won": all_stats.get("win"),
            "draw": all_stats.get("draw"),
            "lost": all_stats.get("lose"),
            "points": r.get("points"),
            "goalsFor": goals.get("for"),
            "goalsAgainst": goals.get("against"),
            "goalDifference": r.get("goalsDiff"),
        })
    return table


def af_fetch_teams(competition_code):
    """
    Returns (competition_meta, teams) where teams are football-data-shaped:
    [{"name":, "shortName":, "crest":}], so fetch_and_cache_team_metadata can
    populate the same cache keys.
    """
    from datetime import date

    league_id = APIFOOTBALL_LEAGUE_IDS.get(competition_code)
    if not league_id:
        return None, []
    season = af_season(competition_code, date.today())
    rows = _af_get("teams", {"league": league_id, "season": season})
    teams = []
    for row in rows:
        team = row.get("team", {}) or {}
        teams.append({
            "name": team.get("name"),
            "shortName": team.get("name"),
            "crest": team.get("logo"),
        })
    return None, teams


# ══ Live-Football-Data (FotMob via RapidAPI) — provider "LF" ══════════════════
# A single /football-get-all-matches-by-league call returns the whole current
# season (finished + upcoming) for a league. We cache that dump for ~a day and
# derive fixtures-by-date, training data, and standings from it — so each league
# costs ~1 request/day, fitting the 100/month free quota.

def _record_lf_request_health(state):
    """Best-effort LF request diagnostics; cache outages must not break provider calls."""
    try:
        cache.set("provider_request_health::LF", state, timeout=60 * 60 * 24)
    except Exception as exc:
        logger.warning("Unable to cache Live-Football-Data diagnostics: %s", exc)


def _lf_get(path):
    """Call Live-Football-Data and return the `response` dict (or {})."""
    if not LF_KEY:
        logger.info("LIVEFOOTBALL_KEY not set; skipping call %s", path)
        _record_lf_request_health({
            "status": "not_configured",
            "last_error": "LIVEFOOTBALL_KEY is not set",
            "checked_at": timezone.now().isoformat(),
        })
        return {}
    headers = {"x-rapidapi-host": LF_HOST, "x-rapidapi-key": LF_KEY}
    try:
        r = requests.get(f"https://{LF_HOST}/{path}", headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.warning("Live-Football-Data request failed: %s error=%s", path, exc)
        _record_lf_request_health({
            "status": "error",
            "last_error": str(exc)[:500],
            "checked_at": timezone.now().isoformat(),
        })
        return {}
    if data.get("status") not in (None, "success"):
        message = str(data.get("message") or data.get("status"))
        logger.warning("Live-Football-Data non-success for %s: %s", path, message)
        _record_lf_request_health({
            "status": "error",
            "last_error": message[:500],
            "checked_at": timezone.now().isoformat(),
        })
    else:
        _record_lf_request_health({
            "status": "ok",
            "last_error": None,
            "checked_at": timezone.now().isoformat(),
        })
    return data.get("response", {}) or {}

def _fotmob_logo(team_id):
    return f"https://images.fotmob.com/image_resources/logo/teamlogo/{team_id}.png" if team_id else None


def _normalize_lf_match(m):
    home = m.get("home", {}) or {}
    away = m.get("away", {}) or {}
    st = m.get("status", {}) or {}
    finished = bool(st.get("finished"))
    if finished:
        status = "FINISHED"
    elif st.get("cancelled"):
        status = "CANCELLED"
    elif st.get("started"):
        status = "IN_PLAY"
    else:
        status = "TIMED"
    return {
        "id": m.get("id"),
        "utcDate": st.get("utcTime"),  # ISO 8601 with Z
        "status": status,
        "homeTeam": {"name": home.get("name"), "crest": _fotmob_logo(home.get("id"))},
        "awayTeam": {"name": away.get("name"), "crest": _fotmob_logo(away.get("id"))},
        "score": {
            "fullTime": {
                "home": home.get("score") if finished else None,
                "away": away.get("score") if finished else None,
            },
            "halfTime": {"home": None, "away": None},
        },
    }


def _lf_season_matches(competition_code, force_refresh=False):
    """Cached full-season match dump (normalised), the single source for LF."""
    league_id = LIVEFOOTBALL_LEAGUE_IDS.get(competition_code)
    if not league_id:
        return []
    ck = f"lf_season_matches::{competition_code}"
    if not force_refresh:
        cached = cache.get(ck)
        if cached is not None:
            return cached
    response = _lf_get(f"football-get-all-matches-by-league?leagueid={league_id}")
    raw = response.get("matches", []) if isinstance(response, dict) else []
    matches = [_normalize_lf_match(m) for m in raw if m.get("home") and m.get("away")]
    cache.set(ck, matches, timeout=LF_SEASON_CACHE_TIMEOUT)
    return matches


def lf_refresh_season(competition_code):
    """Force the once-daily API call (used by the lfrefresh cron job)."""
    return len(_lf_season_matches(competition_code, force_refresh=True))


@_register(providers=["LF"], operation="fetch_matches_by_date")
def lf_fetch_matches_by_date(competition_code, match_date):
    """match_date 'YYYY-MM-DD' — filter the cached season dump by kickoff date."""
    day = str(match_date)[:10]
    return [m for m in _lf_season_matches(competition_code)
            if (m.get("utcDate") or "")[:10] == day]


@_register(providers=["LF"], operation="fetch_matches_by_season")
def lf_fetch_matches_by_season(competition_code, season_year=None):
    """Return the full season dump (training uses the FINISHED ones)."""
    return _lf_season_matches(competition_code)


def _compute_standings_table(matches):
    """
    Build a standings table from a list of normalised match dicts with keys:
        homeTeam.name, awayTeam.name, status, score.fullTime.{home,away}
    Returns a list of row dicts sorted by points/ GD / GF, each with keys:
        position, team.{name,shortName,crest,tla}, playedGames, won, draw, lost,
        points, goalsFor, goalsAgainst, goalDifference
    """
    rows = {}

    def _row(name):
        return rows.setdefault(name, {
            "team": {"name": name, "shortName": name, "crest": None, "tla": None},
            "playedGames": 0, "won": 0, "draw": 0, "lost": 0,
            "points": 0, "goalsFor": 0, "goalsAgainst": 0, "goalDifference": 0,
        })

    for m in matches:
        if m.get("status") != "FINISHED":
            continue
        h, a = m["homeTeam"]["name"], m["awayTeam"]["name"]
        hg, ag = m["score"]["fullTime"]["home"], m["score"]["fullTime"]["away"]
        if not h or not a or hg is None or ag is None:
            continue
        hr, ar = _row(h), _row(a)
        hr["playedGames"] += 1; ar["playedGames"] += 1
        hr["goalsFor"] += hg; hr["goalsAgainst"] += ag
        ar["goalsFor"] += ag; ar["goalsAgainst"] += hg
        if hg > ag:
            hr["won"] += 1; hr["points"] += 3; ar["lost"] += 1
        elif hg < ag:
            ar["won"] += 1; ar["points"] += 3; hr["lost"] += 1
        else:
            hr["draw"] += 1; ar["draw"] += 1; hr["points"] += 1; ar["points"] += 1

    table = list(rows.values())
    for r in table:
        r["goalDifference"] = r["goalsFor"] - r["goalsAgainst"]
    table.sort(key=lambda r: (r["points"], r["goalDifference"], r["goalsFor"]), reverse=True)
    for i, r in enumerate(table, 1):
        r["position"] = i
    return table


@_register(providers=["LF"], operation="fetch_standings")
def lf_fetch_standings(competition_code):
    """Compute standings from the cached LF season dump."""
    return _compute_standings_table(_lf_season_matches(competition_code))


@_register(providers=["LF"], operation="fetch_teams")
def lf_fetch_teams(competition_code):
    """Derive teams (with FotMob crests) from the cached dump — no extra API call."""
    crests = {}
    for m in _lf_season_matches(competition_code):
        for side in ("homeTeam", "awayTeam"):
            n = m[side]["name"]
            if n and n not in crests:
                crests[n] = m[side]["crest"]
    teams = [{"name": n, "shortName": n, "crest": crests[n]} for n in sorted(crests)]
    return None, teams


# ══ football-data.co.uk — provider "UK" ═══════════════════════════════════════
# Free CSV files, no key, no rate limit. Per-season files for main leagues
# (/mmz4281/<season>/<code>.csv) carry results + odds; fixtures.csv lists
# upcoming matches + odds. Training pulls several past seasons (cached long,
# they never change); the current season + fixtures refresh daily.

UK_BASE = "https://www.football-data.co.uk"
UK_FIXTURES_CACHE_TIMEOUT = 60 * 60 * 6
UK_CURRENT_CACHE_TIMEOUT = 60 * 60 * 12
UK_PAST_CACHE_TIMEOUT = 60 * 60 * 24 * 30


def _uk_season_code(season_year):
    """2024 -> '2425' (the 2024/25 season file suffix)."""
    return f"{str(season_year)[-2:]}{str(season_year + 1)[-2:]}"


def _current_season_start_year():
    from datetime import date
    t = date.today()
    return t.year if t.month >= 7 else t.year - 1


def _uk_get_csv(url, timeout=25):
    """Download a CSV into a list of row dicts (pandas handles quoting/BOM)."""
    import io
    import pandas as pd
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), encoding_errors="ignore", on_bad_lines="skip")
        df = df.where(pd.notnull(df), None)
        return df.to_dict("records")
    except Exception as exc:
        logger.warning("football-data.co.uk fetch failed: %s error=%s", url, exc)
        return []


def _uk_iso(date_str, time_str=None):
    """'16/08/2024' (+ '20:00') -> '2024-08-16T20:00:00Z'."""
    from datetime import datetime
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(str(date_str).strip(), fmt)
            break
        except ValueError:
            d = None
    if d is None:
        return None
    t = str(time_str).strip() if time_str else ""
    hh, mm = "00", "00"
    if ":" in t:
        hh, mm = (t.split(":") + ["00"])[:2]
    return f"{d.strftime('%Y-%m-%d')}T{hh.zfill(2)}:{mm.zfill(2)}:00Z"


def _try_float(val):
    """Safely parse a float from a CSV cell (may be empty string)."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _uk_row_to_match(row, fduk_code):
    home = row.get("HomeTeam") or row.get("Home")
    away = row.get("AwayTeam") or row.get("Away")
    if not home or not away:
        return None
    hg = row.get("FTHG", row.get("HG"))
    ag = row.get("FTAG", row.get("AG"))
    hhg, hag = row.get("HTHG"), row.get("HTAG")
    finished = hg is not None and ag is not None
    try:
        hg = int(hg) if finished else None
        ag = int(ag) if finished else None
    except (ValueError, TypeError):
        finished = False
        hg = ag = None
    # Extract betting odds (available in main-league CSVs)
    odds = None
    try:
        avg_h = _try_float(row.get("AvgH"))
        avg_d = _try_float(row.get("AvgD"))
        avg_a = _try_float(row.get("AvgA"))
        over25 = _try_float(row.get("Avg>2.5") or row.get("B365>2.5"))
        if avg_h and avg_d and avg_a:
            odds = {"avgH": avg_h, "avgD": avg_d, "avgA": avg_a}
            if over25:
                odds["over25"] = over25
    except Exception:
        odds = None

    return {
        "id": f"UK-{fduk_code}-{row.get('Date')}-{home}-{away}",
        "utcDate": _uk_iso(row.get("Date"), row.get("Time")),
        "status": "FINISHED" if finished else "TIMED",
        "homeTeam": {"name": str(home).strip(), "crest": None},
        "awayTeam": {"name": str(away).strip(), "crest": None},
        "odds": odds,
        "score": {
            "fullTime": {"home": hg, "away": ag},
            "halfTime": {
                "home": int(hhg) if str(hhg).isdigit() else None,
                "away": int(hag) if str(hag).isdigit() else None,
            },
        },
    }


def _uk_extra_matches(fduk_code):
    """All matches from /new/CODE.csv (single combined file). Cached."""
    ck = f"uk_extra::{fduk_code}"
    cached = cache.get(ck)
    if cached is not None:
        return cached
    url = f"{UK_BASE}/new/{fduk_code}.csv"
    rows = _uk_get_csv(url)
    matches = [m for m in (_uk_row_to_match(r, fduk_code) for r in rows) if m]
    cache.set(ck, matches, timeout=UK_CURRENT_CACHE_TIMEOUT)
    return matches


def _uk_main_season(fduk_code, season_year):
    """Results for one season of a main league (cached; past seasons long)."""
    ck = f"uk_main::{fduk_code}::{season_year}"
    cached = cache.get(ck)
    if cached is not None:
        return cached
    url = f"{UK_BASE}/mmz4281/{_uk_season_code(season_year)}/{fduk_code}.csv"
    rows = _uk_get_csv(url)
    matches = [m for m in (_uk_row_to_match(r, fduk_code) for r in rows) if m]
    timeout = (UK_CURRENT_CACHE_TIMEOUT if season_year >= _current_season_start_year()
               else UK_PAST_CACHE_TIMEOUT)
    cache.set(ck, matches, timeout=timeout)
    return matches


def _uk_fixtures(fduk_code):
    """Upcoming fixtures for a league from the shared fixtures.csv (cached)."""
    ck = "uk_fixtures_all_v2"
    allrows = cache.get(ck)
    if allrows is None:
        allrows = _uk_get_csv(f"{UK_BASE}/fixtures.csv")
        cache.set(ck, allrows, timeout=UK_FIXTURES_CACHE_TIMEOUT)
    out = []
    for r in allrows:
        if (r.get("Div") or "").strip() == fduk_code:
            m = _uk_row_to_match(r, fduk_code)
            if m:
                m["status"] = "TIMED"  # fixtures file = upcoming, no scores
                out.append(m)
    return out


def _uk_current_matches(competition_code):
    """Current-season results + upcoming fixtures, normalised and cached."""
    mapping = FOOTBALL_DATA_UK_CODES.get(competition_code)
    if not mapping:
        return []
    kind, fduk_code = mapping
    ck = f"uk_current_v2::{competition_code}"
    cached = cache.get(ck)
    if cached is not None:
        return cached

    if kind == "extra":
        # Extra leagues: single combined file, filter to current season
        all_matches = _uk_extra_matches(fduk_code)
        season = _current_season_start_year()
        season_matches = [m for m in all_matches
                          if m.get("utcDate", "").startswith(str(season))
                          or m.get("utcDate", "").startswith(str(season + 1))]
        if len(season_matches) < 10:
            season_matches = [m for m in all_matches
                              if m.get("utcDate", "").startswith(str(season - 1))
                              or m.get("utcDate", "").startswith(str(season))]
    else:
        season = _current_season_start_year()
        season_matches = _uk_main_season(fduk_code, season)
        if len(season_matches) < 10:
            logger.info("UK season %d empty for %s (%d matches) — falling back to %d",
                        season, fduk_code, len(season_matches), season - 1)
            season_matches = _uk_main_season(fduk_code, season - 1)

    matches = season_matches + _uk_fixtures(fduk_code)
    cache.set(ck, matches, timeout=UK_CURRENT_CACHE_TIMEOUT)
    return matches


@_register(providers=["UK"], operation="fetch_matches_by_date")
def uk_fetch_matches_by_date(competition_code, match_date):
    day = str(match_date)[:10]
    return [m for m in _uk_current_matches(competition_code)
            if (m.get("utcDate") or "")[:10] == day]


@_register(providers=["UK"], operation="fetch_matches_by_season")
def uk_fetch_matches_by_season(competition_code, season_year):
    """Past/current season results for training."""
    mapping = FOOTBALL_DATA_UK_CODES.get(competition_code)
    if not mapping:
        return []
    kind, fduk_code = mapping
    if kind == "extra":
        all_matches = _uk_extra_matches(fduk_code)
        if season_year is None:
            season_year = _current_season_start_year()
        return [m for m in all_matches if m["status"] == "FINISHED" and (
            (m.get("utcDate", "") or "").startswith(str(season_year)) or
            (m.get("utcDate", "") or "").startswith(str(season_year + 1)))]
    if season_year is None:
        season_year = _current_season_start_year()
    return [m for m in _uk_main_season(fduk_code, season_year) if m["status"] == "FINISHED"]


@_register(providers=["UK"], operation="fetch_standings")
def uk_fetch_standings(competition_code):
    """Compute standings from ONLY the current season's finished matches."""
    mapping = FOOTBALL_DATA_UK_CODES.get(competition_code)
    if not mapping:
        return []
    _, fduk_code = mapping
    from datetime import date
    t = date.today()
    season = t.year if t.month >= 7 else t.year - 1
    matches = _uk_main_season(fduk_code, season)
    finished = [m for m in matches if m["status"] == "FINISHED"]
    return _compute_standings_table(finished)


@_register(providers=["UK"], operation="fetch_teams")
def uk_fetch_teams(competition_code):
    names = set()
    for m in _uk_current_matches(competition_code):
        if m["homeTeam"]["name"]:
            names.add(m["homeTeam"]["name"])
        if m["awayTeam"]["name"]:
            names.add(m["awayTeam"]["name"])
    teams = [{"name": n, "shortName": n, "crest": None} for n in sorted(names)]
    return None, teams




# ══ open-matchday — provider "OM" (non-commercial fallback) ══════════════════
# CSV schema: date,time,home,away,ft_home,ft_away,... . Empty score cells are
# unavailable values, never zero. The feed publishes the current season only.
# This is not a live-score source.

def supports_open_matchday(competition_code):
    return OM_ENABLED and competition_code in OPEN_MATCHDAY_COMPETITIONS


def supports_openfootball(competition_code):
    return OF_ENABLED and competition_code in OPENFOOTBALL_COMPETITIONS


def _om_season_code(season_year):
    """Return the current published season code (e.g. 2026 -> '2627')."""
    from datetime import date
    today = date.today()
    current_start = today.year if today.month >= 7 else today.year - 1
    if season_year is not None and int(season_year) != current_start:
        return None
    return f"{str(current_start)[-2:]}{str(current_start + 1)[-2:]}"


def _om_get_csv(competition_code, kind, season_code):
    """Fetch one published CSV and return its rows, cached to avoid repeat downloads."""
    import csv
    import io

    folder = OPEN_MATCHDAY_COMPETITIONS.get(competition_code)
    if not folder or not supports_open_matchday(competition_code):
        return []
    if kind not in {"results", "fixtures"}:
        return []
    cache_key = f"om_csv_v2::{competition_code}::{kind}::{season_code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{OM_BASE_URL}/{folder}/{kind}_{season_code}.csv"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "IX-Tips-Predictions/1.0 (non-commercial research)"},
            timeout=20,
        )
        if response.status_code == 404:
            rows = []
        else:
            response.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    except Exception as exc:
        logger.warning("open-matchday fetch failed for %s %s: %s", competition_code, kind, exc)
        rows = []

    timeout = OM_CACHE_TIMEOUT if kind == "results" else OM_FIXTURE_CACHE_TIMEOUT
    cache.set(cache_key, rows, timeout=timeout)
    return rows


def _om_int(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _om_row_to_match(row, competition_code, season_code, kind):
    home = (row.get("home") or "").strip()
    away = (row.get("away") or "").strip()
    match_day = (row.get("date") or "").strip()
    if not home or not away or not match_day:
        return None

    # Keep the published UTC calendar date intact. The feed's kickoff clock
    # uses a fixed UTC+01:00 convention; when it is absent, do not invent noon.
    kickoff = (row.get("time") or "").strip()
    if kickoff:
        try:
            from datetime import datetime as _datetime
            parsed_kickoff = _datetime.strptime(kickoff[:5], "%H:%M")
            utc_date = f"{match_day}T{parsed_kickoff:%H:%M}:00+01:00"
        except ValueError:
            utc_date = match_day
    else:
        utc_date = match_day

    home_goals = _om_int(row.get("ft_home")) if kind == "results" else None
    away_goals = _om_int(row.get("ft_away")) if kind == "results" else None
    finished = home_goals is not None and away_goals is not None
    # MatchPrediction.match_id is varchar(50). Hash the source identity rather
    # than storing unbounded team names in that unique database column.
    identity = "|".join((
        str(competition_code), str(season_code), match_day,
        home.casefold(), away.casefold(),
    ))
    stable_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"OM-{competition_code}-{stable_id}",
        "utcDate": utc_date,
        "status": "FINISHED" if finished else "TIMED",
        "homeTeam": {"name": home, "crest": None},
        "awayTeam": {"name": away, "crest": None},
        "score": {
            "fullTime": {"home": home_goals, "away": away_goals},
            "halfTime": {
                "home": _om_int(row.get("ht_home")),
                "away": _om_int(row.get("ht_away")),
            },
        },
    }


def _om_current_matches(competition_code):
    """Return current-season results plus fixtures, preferring finished results."""
    if not supports_open_matchday(competition_code):
        return []
    from datetime import date
    today = date.today()
    current_start = today.year if today.month >= 7 else today.year - 1
    season_code = _om_season_code(current_start)
    if not season_code:
        return []

    cache_key = f"om_current_matches_v2::{competition_code}::{season_code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    matches = []
    for kind in ("results", "fixtures"):
        rows = _om_get_csv(competition_code, kind, season_code)
        matches.extend(
            item for item in (
                _om_row_to_match(row, competition_code, season_code, kind)
                for row in rows
            ) if item
        )

    by_key = {}
    for match in matches:
        key = (
            (match.get("utcDate") or "")[:10],
            (match.get("homeTeam") or {}).get("name", "").casefold(),
            (match.get("awayTeam") or {}).get("name", "").casefold(),
        )
        existing = by_key.get(key)
        if existing is None or (
            match.get("status") == "FINISHED" and existing.get("status") != "FINISHED"
        ):
            by_key[key] = match
    result = sorted(
        by_key.values(),
        key=lambda item: (item.get("utcDate") or "", (item.get("homeTeam") or {}).get("name") or ""),
    )
    cache.set(cache_key, result, timeout=OM_FIXTURE_CACHE_TIMEOUT)
    return result


@_register(providers=["OM"], operation="fetch_matches_by_date")
def om_fetch_matches_by_date(competition_code, match_date):
    day = str(match_date)[:10]
    return [
        match for match in _om_current_matches(competition_code)
        if (match.get("utcDate") or "")[:10] == day
    ]


@_register(providers=["OM"], operation="fetch_matches_by_season")
def om_fetch_matches_by_season(competition_code, season_year):
    """Return current published-season results; older seasons are not supplied."""
    season_code = _om_season_code(season_year)
    if not season_code or not supports_open_matchday(competition_code):
        return []
    return [
        match for match in _om_current_matches(competition_code)
        if match.get("status") == "FINISHED"
    ]


# ══ OpenFootball (OF) — CC0 public-domain African match files ════════════════
@_register(providers=["OF"], operation="fetch_matches_by_date")
def of_fetch_matches_by_date(competition_code, match_date):
    from .openfootball import fetch_matches_by_date
    return fetch_matches_by_date(competition_code, match_date)


@_register(providers=["OF"], operation="fetch_matches_by_season")
def of_fetch_matches_by_season(competition_code, season_year):
    from .openfootball import fetch_matches_by_season
    return fetch_matches_by_season(competition_code, season_year)


@_register(providers=["OF"], operation="fetch_standings")
def of_fetch_standings(competition_code):
    from .openfootball import fetch_standings
    return fetch_standings(competition_code)


@_register(providers=["OF"], operation="fetch_teams")
def of_fetch_teams(competition_code):
    from .openfootball import fetch_teams
    return None, fetch_teams(competition_code)


@_register(providers=["OF"], operation="fetch_scorers")
def of_fetch_scorers(competition_code):
    return []


def of_current_matches(competition_code):
    from .openfootball import current_matches
    return current_matches(competition_code)


# ══ FD provider — thin wrappers around football-data.org's REST API ══════════
# These are registered in the same dispatch table so all fetcher functions can
# use dispatch_provider() instead of manual if/elif chains.

def _fd_get(path, params=None, retries=2):
    """Call football-data.org and return parsed JSON (or {} on failure)."""
    import time as _time
    url = f"{BASE_URL}/{path}"
    headers = {"X-Auth-Token": API_TOKEN}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                _time.sleep(2 * (attempt + 1))
        except Exception:
            _time.sleep(2)
    return {}


def _fd_normalize_match(m):
    """Normalize a football-data.org match object into the internal shape."""
    from datetime import datetime as _dt
    home = m.get("homeTeam", {}) or {}
    away = m.get("awayTeam", {}) or {}
    score = m.get("score", {}) or {}
    ft = score.get("fullTime", {}) or {}
    ht = score.get("halfTime", {}) or {}
    utc = m.get("utcDate")
    if utc:
        try:
            parsed = _dt.fromisoformat(utc.replace("Z", "+00:00"))
            utc = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return {
        "id": m.get("id"),
        "utcDate": utc,
        "status": m.get("status", "TIMED"),
        "homeTeam": {"name": home.get("name"), "crest": home.get("crest")},
        "awayTeam": {"name": away.get("name"), "crest": away.get("crest")},
        "score": {
            "fullTime": {"home": ft.get("home"), "away": ft.get("away")},
            "halfTime": {"home": ht.get("home"), "away": ht.get("away")},
        },
    }


@_register(providers=["FD"], operation="fetch_matches_by_date")
def fd_fetch_matches_by_date(competition_code, match_date):
    from datetime import datetime as _dt, timedelta as _td
    d = _dt.strptime(str(match_date)[:10], "%Y-%m-%d")
    params = {"competitions": competition_code,
              "dateFrom": d.strftime("%Y-%m-%d"),
              "dateTo": (d + _td(days=1)).strftime("%Y-%m-%d")}
    data = _fd_get("matches", params)
    return [_fd_normalize_match(m) for m in data.get("matches", [])]


@_register(providers=["FD"], operation="fetch_matches_by_season")
def fd_fetch_matches_by_season(competition_code, season_year):
    data = _fd_get(f"competitions/{competition_code}/matches", {"season": season_year})
    return [_fd_normalize_match(m) for m in data.get("matches", [])]


@_register(providers=["FD"], operation="fetch_standings")
def fd_fetch_standings(competition_code):
    from datetime import date
    t = date.today()
    season = t.year if t.month >= 7 else t.year - 1
    data = _fd_get(f"competitions/{competition_code}/standings",
                   params={"season": season})
    try:
        return data.get("standings", [])[0].get("table", [])
    except (IndexError, KeyError, TypeError):
        return []


@_register(providers=["FD"], operation="fetch_teams")
def fd_fetch_teams(competition_code):
    data = _fd_get(f"competitions/{competition_code}/teams")
    teams = data.get("teams", []) or []
    comp_meta = {
        "name": (data.get("competition", {}) or {}).get("name", ""),
        "crest": (data.get("competition", {}) or {}).get("emblem", ""),
    }
    return comp_meta, teams


@_register(providers=["FD"], operation="fetch_scorers")
def fd_fetch_scorers(competition_code):
    data = _fd_get(f"competitions/{competition_code}/scorers")
    return data.get("scorers", []) or []
