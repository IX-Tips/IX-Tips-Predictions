"""Date-scoped fallback adapters for Football Charts and SportsEvents24.

Both adapters normalize upcoming fixtures into the football-data.org shape.
They deliberately do not provide training history, odds, standings or results.
"""
import hashlib
import logging
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone as dt_timezone

import requests
from django.core.cache import cache

from .constants import COMPETITIONS
from .providers import _register

logger = logging.getLogger(__name__)

FC_BASE_URL = os.environ.get("FOOTBALL_CHARTS_BASE_URL", "https://api.football-charts.com/api/v1").rstrip("/")
FC_KEY = os.environ.get("FOOTBALL_CHARTS_API_KEY") or os.environ.get("FC_API_KEY", "")
SE_BASE_URL = os.environ.get("SPORTEVENTS_BASE_URL", "https://sportsevents24.com/api/v1").rstrip("/")
FC_CATALOG_TTL = 60 * 60 * 24
FC_FIXTURE_TTL = 60 * 60 * 23
FC_EMPTY_FIXTURE_TTL = 60 * 45
SE_TOURNAMENT_TTL = 60 * 60 * 24
SE_FIXTURE_TTL = 60 * 60 * 8

# These exact aliases are based on the published Football Charts league slugs.
_FC_SLUG_OVERRIDES = {"ALG": "algir1", "EGY": "egypt", "MAR": "morocco"}
_FC_NAME_ALIASES = {
    "ANG": "Girabola", "BEN": "Ligue Pro", "BFA": "Ligue 1", "BDI": "Ligue A",
    "GHA": "Premier League", "KEN": "FKF Premier League", "LBR": "First Division",
    "MWI": "Super League", "MTN": "Ligue 1", "NGA": "NPFL",
    "RWA": "Premier League", "SEN": "Ligue 1", "ZAF": "Premiership",
    "TZA": "Ligi Kuu Bara", "UGA": "Premier League", "ZMB": "Super League",
    "ZWE": "Premier Soccer League",
}
_FC_COUNTRIES = {
    "ANG": "Angola", "BEN": "Benin", "BFA": "Burkina Faso", "BDI": "Burundi",
    "GHA": "Ghana", "KEN": "Kenya", "LBR": "Liberia", "MWI": "Malawi",
    "MTN": "Mauritania", "NGA": "Nigeria", "RWA": "Rwanda", "SEN": "Senegal",
    "ZAF": "South Africa", "TZA": "Tanzania", "UGA": "Uganda",
    "ZMB": "Zambia", "ZWE": "Zimbabwe",
}
# SportsEvents24 uses ISO-3166 alpha-2 country filters. Keep this restricted
# to competitions in the African coverage set; do not guess country codes.
_SE_COUNTRIES = {
    "ALG": "DZ", "ANG": "AO", "BEN": "BJ", "BFA": "BF", "BDI": "BI",
    "EGY": "EG", "GHA": "GH", "LBR": "LR", "MWI": "MW",
    "MAR": "MA", "MTN": "MR", "RWA": "RW", "SEN": "SN",
    "ZAF": "ZA", "TZA": "TZ", "ZMB": "ZM", "ZWE": "ZW",
}

def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()

def supports_football_charts(competition_code):
    return bool(FC_KEY) and str(competition_code or "").upper() in COMPETITIONS

def supports_sportsevents(competition_code):
    return str(competition_code or "").upper() in _SE_COUNTRIES

def _get_json(url, *, headers=None, params=None, timeout=15):
    response = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Provider returned a non-object JSON response")
    if payload.get("error") or payload.get("errors"):
        raise RuntimeError(str(payload.get("error") or payload.get("errors"))[:400])
    return payload

def _fc_headers():
    return {"Authorization": f"Bearer {FC_KEY}", "Accept": "application/json",
            "User-Agent": "IX-Tips-Predictions/1.0"}

def _fc_catalog():
    cached = cache.get("football_charts_catalog_v1")
    if cached is not None:
        return cached
    payload = _get_json(f"{FC_BASE_URL}/leagues/", headers=_fc_headers())
    leagues = payload.get("leagues")
    if not isinstance(leagues, list):
        raise ValueError("Football Charts league catalogue has no leagues list")
    cache.set("football_charts_catalog_v1", leagues, timeout=FC_CATALOG_TTL)
    return leagues

def _fc_league_slug(competition_code):
    code = str(competition_code or "").upper()
    override = _FC_SLUG_OVERRIDES.get(code)
    catalog = _fc_catalog()
    if override:
        return override if any(str(x.get("league")) == override for x in catalog) else None
    wanted = _norm(_FC_NAME_ALIASES.get(code) or COMPETITIONS.get(code))
    expected_country = _norm(_FC_COUNTRIES.get(code))
    matches = [
        x for x in catalog
        if _norm(x.get("name")) == wanted
        and (not expected_country or _norm(x.get("country")) == expected_country)
    ]
    if len(matches) == 1:
        return matches[0].get("league")
    # Never guess between similarly named leagues in different countries.
    return None

def _parse_fc_match(item, competition_code):
    home = str(item.get("home_team") or "").strip()
    away = str(item.get("away_team") or "").strip()
    day = str(item.get("match_date") or "")[:10]
    if not home or not away or len(day) != 10:
        return None
    clock = str(item.get("time") or "").strip()
    if clock:
        try:
            parsed = datetime.strptime(clock[:5], "%H:%M")
            kickoff = f"{day}T{parsed:%H:%M}:00Z"
        except ValueError:
            kickoff = day
    else:
        kickoff = day
    status_raw = str(item.get("status") or "scheduled").casefold()
    status = "POSTPONED" if "postpon" in status_raw else (
        "CANCELLED" if "cancel" in status_raw else "TIMED"
    )
    identity = str(item.get("slug") or "|".join((competition_code, day, home.casefold(), away.casefold())))
    stable = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return {"id": f"FC-{stable}", "utcDate": kickoff, "status": status,
            "homeTeam": {"name": home, "crest": None},
            "awayTeam": {"name": away, "crest": None},
            "score": {"fullTime": {"home": None, "away": None}},
            "attribution": "Data by football-charts.com"}

@_register(providers=["FC"], operation="fetch_matches_by_date")
def football_charts_fetch_matches_by_date(competition_code, match_date):
    if not supports_football_charts(competition_code):
        return []
    slug = _fc_league_slug(competition_code)
    if not slug:
        logger.info("Football Charts has no unambiguous league mapping for %s", competition_code)
        return []
    day = date.fromisoformat(str(match_date)[:10])
    cache_key = f"football_charts_fixtures_v1::{slug}"
    cached = cache.get(cache_key)
    if cached is None:
        payload = _get_json(f"{FC_BASE_URL}/leagues/{slug}/fixtures/", headers=_fc_headers())
        rows = payload.get("matches")
        if not isinstance(rows, list):
            raise ValueError(f"Football Charts fixtures response malformed for {slug}")
        cached = [
            parsed for parsed in (_parse_fc_match(row, competition_code) for row in rows)
            if parsed
        ]
        if not cached:
            # An empty response is often temporary: Football Charts may publish
            # fixtures after its daily feed refresh. Do not suppress those updates
            # for almost a full day.
            logger.info(
                "Football Charts returned no fixtures for %s (%s): count=%s, "
                "next_fixture_date=%s, note=%s",
                competition_code,
                slug,
                payload.get("count"),
                payload.get("next_fixture_date"),
                str(payload.get("note") or "")[:300],
            )
            cache_ttl = FC_EMPTY_FIXTURE_TTL
        else:
            # Retain the long cache for populated windows to conserve the free
            # account's daily request quota.
            cache_ttl = FC_FIXTURE_TTL
        cache.set(cache_key, cached, timeout=cache_ttl)
    return [match for match in cached if (match.get("utcDate") or "")[:10] == day.isoformat()]

def _se_tournaments(country):
    key = f"sportsevents_tournaments_v1::{country}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = _get_json(f"{SE_BASE_URL}/tournaments", params={"country": country, "sport_id": 1})
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("SportsEvents24 tournaments response malformed")
    cache.set(key, rows, timeout=SE_TOURNAMENT_TTL)
    return rows

# Explicit catalogue names confirmed by the read-only SportsEvents24 audit.
# Keep aliases exact after normalization; do not fuzzy-match cups, youth or
# women's competitions into senior domestic leagues.
_SE_NAME_ALIASES = {
    "ALG": ("Ligue 1",),
    "ANG": ("Girabola",),
    "BEN": ("Ligue Pro",),
    "BFA": ("Ligue 1",),
    "BDI": ("Ligue A",),
    "EGY": ("Premier League", "Egyptian Premier League 2026/2027"),
    "GHA": ("Premier League", "Ghana Premier League 2026/2027"),
    "KEN": ("FKF Premier League",),
    "LBR": ("First Division",),
    "MWI": ("Super League", "FDH Bank Premiership", "FDH Bank Premiership 2026/2027"),
    "MAR": ("Botola Pro", "Botola Pro D1 2026/2027"),
    "MTN": ("Ligue 1", "FFRIM Super D1", "FFRIM Super D1 2026/2027"),
    "NGA": ("NPFL",),
    "RWA": ("Premier League",),
    "SEN": ("Ligue 1", "Ligue 1 Sénégal", "Ligue 1 Sénégal 2026/2027"),
    "ZAF": ("Premiership", "Betway Premiership", "Betway Premiership 2026/27"),
    "TZA": ("Ligi Kuu Bara", "NBC Premier League", "NBC Premier League 2026/27"),
    "UGA": ("Premier League",),
    "ZMB": ("Super League",),
    "ZWE": ("Premier Soccer League",),
}

def _se_tournament_id(competition_code):
    code = str(competition_code or "").upper()
    country = _SE_COUNTRIES.get(code)
    if not country:
        return None
    aliases = _SE_NAME_ALIASES.get(code) or (COMPETITIONS.get(code),)
    wanted = {_norm(alias) for alias in aliases if alias}
    rows = _se_tournaments(country)
    candidates = []
    for row in rows:
        title = row.get("name") or row.get("tournament") or row.get("title") or row.get("tournamentName") or row.get("tournament_name")
        if isinstance(title, dict):
            title = title.get("name") or title.get("title")
        if _norm(title) in wanted:
            candidates.append(row)
    # South Africa currently exposes duplicate/season-labelled Betway
    # Premiership entries. Refuse to choose until fixture feeds are compared.
    if len(candidates) == 1:
        return candidates[0].get("id") or candidates[0].get("tournamentId")
    # Avoid fuzzy tournament selection: it can silently mix cups and leagues.
    return None

def _parse_se_event(item, competition_code):
    home_value = item.get("teamHome") or ""
    away_value = item.get("teamAway") or ""
    home = str(home_value.get("name") if isinstance(home_value, dict) else home_value).strip()
    away = str(away_value.get("name") if isinstance(away_value, dict) else away_value).strip()
    starts = item.get("startsAt")
    if not home or not away or not starts:
        return None
    try:
        kickoff = datetime.fromisoformat(str(starts).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            return None
        kickoff = kickoff.astimezone(dt_timezone.utc)
    except (TypeError, ValueError):
        return None
    result = item.get("result")
    home_goals = away_goals = None
    if isinstance(result, str):
        score = re.fullmatch(r"\s*(\d+)\s*[:\-]\s*(\d+)\s*", result)
        if score:
            home_goals, away_goals = int(score.group(1)), int(score.group(2))
    status = "FINISHED" if home_goals is not None and away_goals is not None else "TIMED"
    identity = str(item.get("id") or "|".join((competition_code, kickoff.isoformat(), home, away)))
    stable = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return {"id": f"SE-{stable}", "utcDate": kickoff.isoformat().replace("+00:00", "Z"),
            "status": status, "homeTeam": {"name": home, "crest": None},
            "awayTeam": {"name": away, "crest": None},
            "score": {"fullTime": {"home": home_goals, "away": away_goals}}}

@_register(providers=["SE"], operation="fetch_matches_by_date")
def sportsevents_fetch_matches_by_date(competition_code, match_date):
    if not supports_sportsevents(competition_code):
        return []
    tournament_id = _se_tournament_id(competition_code)
    if tournament_id is None:
        logger.info("SportsEvents24 has no exact tournament mapping for %s", competition_code)
        return []
    day = date.fromisoformat(str(match_date)[:10])
    # The API permits dates from the previous 7 days through the next 30 days.
    today = date.today()
    if day < today - timedelta(days=7) or day > today + timedelta(days=30):
        return []
    if day >= today:
        window_start = today
        window_end = min(today + timedelta(days=7), today + timedelta(days=30))
    else:
        window_start = day
        window_end = day + timedelta(days=1)
    key = f"sportsevents_fixtures_v1::{tournament_id}::{window_start.isoformat()}::{window_end.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return [match for match in cached if (match.get("utcDate") or "")[:10] == day.isoformat()]
    payload = _get_json(f"{SE_BASE_URL}/events", params={
        "tournament_id": tournament_id, "from": window_start.isoformat(),
        "to": window_end.isoformat(), "limit": 100,
    })
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("SportsEvents24 events response malformed")
    matches = [
        parsed for parsed in (_parse_se_event(row, competition_code) for row in rows)
        if parsed
    ]
    # Cache the complete seven-day window for eight hours. This keeps the combined
    # tournament-discovery and fixture calls below the 100-request daily cap
    # for the supported African competitions.
    cache.set(key, matches, timeout=SE_FIXTURE_TTL)
    return [match for match in matches if (match.get("utcDate") or "")[:10] == day.isoformat()]
