"""
Multi-provider fixture aggregation.

Historical match ingestion and upcoming fixture discovery are deliberately
separate. COMPETITION_PROVIDERS continues to choose the primary source for
historical data and league metadata. Fixture sources can be configured
independently with FIXTURE_PROVIDER_ORDER.

Example:
    FIXTURE_PROVIDER_ORDER='{"BEL":["UK","AF"],"TUR":["UK","AF"]}'
"""
import json
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timezone as datetime_timezone

from django.core.cache import cache
from django.utils import timezone

from .providers import (
    dispatch_provider, get_provider, AF_KEY, LF_KEY, API_TOKEN,
    supports_open_matchday, supports_openfootball, LIVEFOOTBALL_LEAGUE_IDS,
)
from .constants import APIFOOTBALL_LEAGUE_IDS, APIFOOTBALL_LEAGUE_SEARCH

logger = logging.getLogger(__name__)

try:
    FIXTURE_PROVIDER_ORDER = json.loads(os.getenv("FIXTURE_PROVIDER_ORDER", "{}"))
    if not isinstance(FIXTURE_PROVIDER_ORDER, dict):
        FIXTURE_PROVIDER_ORDER = {}
except (TypeError, ValueError, json.JSONDecodeError):
    logger.exception("Invalid FIXTURE_PROVIDER_ORDER JSON; using primary providers")
    FIXTURE_PROVIDER_ORDER = {}



PROVIDER_DIAGNOSTIC_TTL = 60 * 60 * 24 * 14


def provider_diagnostic_key(competition_code, provider):
    return f"provider_diagnostic::{competition_code}::{provider}"


def provider_configuration(provider, competition_code):
    """Return safe credential/configuration state without exposing secrets."""
    if provider == "FD":
        return bool(API_TOKEN), "FOOTBALL_DATA_API_KEY"
    if provider == "UK":
        return True, "Public CSV source; no key required"
    if provider == "LF":
        return bool(LF_KEY and competition_code in LIVEFOOTBALL_LEAGUE_IDS), "LIVEFOOTBALL_KEY"
    if provider == "AF":
        return bool(AF_KEY), "APIFOOTBALL_KEY"
    if provider == "OM":
        return supports_open_matchday(competition_code), "OPEN_MATCHDAY_ENABLED"
    if provider == "OF":
        return supports_openfootball(competition_code), "OpenFootball CC0 public-domain data; no key required"
    if provider == "RSSSF":
        from .rsssf import RSSSF_COMPETITIONS
        return str(competition_code or "").upper() in RSSSF_COMPETITIONS, "RSSSF public current-season fixtures; no key required"
    if provider == "NPFL":
        from .providers import supports_npfl
        return supports_npfl(competition_code), "NPFL public fixtures; no key required"
    if provider == "FKF":
        from .providers import supports_fkf
        return supports_fkf(competition_code), "FKF public fixtures; no key required"
    return False, "Unknown provider"


def _diagnostic_cache_get(key, default=None):
    """Diagnostics must never interrupt fixture retrieval if cache storage is unavailable."""
    try:
        return cache.get(key, default)
    except Exception as exc:
        logger.warning("Provider diagnostics cache read failed for %s: %s", key, exc)
        return default


def _diagnostic_cache_set(key, value, timeout=PROVIDER_DIAGNOSTIC_TTL):
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception as exc:
        logger.warning("Provider diagnostics cache write failed for %s: %s", key, exc)
        return False


def record_provider_diagnostic(competition_code, provider, status, fixture_count=0, error=None, match_date=None):
    """Persist the latest provider attempt without allowing cache failures to break predictions."""
    key = provider_diagnostic_key(competition_code, provider)
    previous = _diagnostic_cache_get(key, {}) or {}
    now = timezone.now().isoformat()
    state = {
        "competition": competition_code,
        "provider": provider,
        "status": status,
        "fixture_count": int(fixture_count or 0),
        "last_checked": now,
        "last_match_date": str(match_date) if match_date else None,
        "last_error": (str(error)[:500] if error else None),
        "last_success": previous.get("last_success"),
    }
    if status == "fixtures_found" and fixture_count:
        state["last_success"] = now
    _diagnostic_cache_set(key, state, timeout=PROVIDER_DIAGNOSTIC_TTL)
    return state


def get_provider_diagnostic(competition_code, provider):
    return _diagnostic_cache_get(provider_diagnostic_key(competition_code, provider))


def result_provider_diagnostic_key(competition_code, provider):
    return f"result_provider_diagnostic::{competition_code}::{provider}"


def record_result_provider_diagnostic(competition_code, provider, status, result_count=0, error=None, match_date=None):
    """Keep result-feed health separate from fixture-discovery diagnostics."""
    key = result_provider_diagnostic_key(competition_code, provider)
    previous = _diagnostic_cache_get(key, {}) or {}
    now = timezone.now().isoformat()
    state = {
        "competition": competition_code,
        "provider": provider,
        "status": status,
        "result_count": int(result_count or 0),
        "last_checked": now,
        "last_match_date": str(match_date) if match_date else None,
        "last_error": str(error)[:500] if error else None,
        "last_result_success": previous.get("last_result_success"),
    }
    if status == "results_found" and result_count:
        state["last_result_success"] = now
    _diagnostic_cache_set(key, state, timeout=PROVIDER_DIAGNOSTIC_TTL)
    return state


def get_result_provider_diagnostic(competition_code, provider):
    return _diagnostic_cache_get(result_provider_diagnostic_key(competition_code, provider))


def _result_provider_order(competition_code):
    """Return every enabled result-capable source mapped to this competition."""
    from . import providers

    ordered = []
    for provider in [get_provider(competition_code), *_provider_order(competition_code)]:
        if provider not in ordered:
            ordered.append(provider)

    # Add independent result feeds only where the competition has an explicit
    # mapping. This avoids speculative calls to providers that cannot cover it.
    if competition_code in providers.FOOTBALL_DATA_UK_CODES:
        ordered.append("UK")
    if providers.LF_KEY and competition_code in LIVEFOOTBALL_LEAGUE_IDS:
        ordered.append("LF")
    if providers.AF_KEY and competition_code in APIFOOTBALL_LEAGUE_IDS:
        ordered.append("AF")
    if supports_open_matchday(competition_code):
        ordered.append("OM")
    if supports_openfootball(competition_code):
        ordered.append("OF")

    result = []
    for provider in ordered:
        if provider in result:
            continue
        if providers._PROVIDER_DISPATCH.get(provider, {}).get("fetch_matches_by_date") is not None:
            result.append(provider)
    return result


def fetch_result_matches_by_date(competition_code, match_date, target_pairs=None):
    """Merge result feeds, stopping once every requested saved fixture has a score.

    ``target_pairs`` contains normalized (home, away) identities for saved
    predictions being reconciled. When omitted, retain full-feed behavior.
    """
    merged = {}
    targets = set(target_pairs or ())
    for provider in _result_provider_order(competition_code):
        configured, credential_name = provider_configuration(provider, competition_code)
        if not configured:
            record_result_provider_diagnostic(
                competition_code, provider, "not_configured",
                error=f"{credential_name} is missing, disabled, or not mapped",
                match_date=match_date,
            )
            continue
        try:
            matches = _dispatch_explicit_provider(provider, competition_code, match_date) or []
        except Exception as exc:
            record_result_provider_diagnostic(
                competition_code, provider, "error", error=exc, match_date=match_date,
            )
            logger.warning(
                "Result provider %s failed for %s on %s: %s",
                provider, competition_code, match_date, exc,
            )
            continue

        result_count = 0
        for match in matches:
            key = _matching_fixture_key(merged, match)
            if key is None:
                continue
            has_result = _has_result(match)
            if has_result:
                result_count += 1
            existing = merged.get(key)
            if existing is None or (has_result and not _has_result(existing)):
                merged[key] = match

        record_result_provider_diagnostic(
            competition_code, provider,
            "results_found" if result_count else ("fixtures_found_no_results" if matches else "empty_response"),
            result_count=result_count, match_date=match_date,
        )

        if targets:
            resolved = {
                (_team_key((item.get("homeTeam") or {}).get("name")),
                 _team_key((item.get("awayTeam") or {}).get("name")))
                for item in merged.values()
                if _has_result(item)
            }
            if targets.issubset(resolved):
                break

    return sorted(
        merged.values(),
        key=lambda item: (item.get("utcDate") or "", (item.get("homeTeam") or {}).get("name") or ""),
    )

def _provider_order(competition_code):
    configured = FIXTURE_PROVIDER_ORDER.get(competition_code)
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, (list, tuple)):
        configured = [get_provider(competition_code)]

    providers = []
    for provider in configured:
        provider = str(provider).strip().upper()
        if provider in {"FD", "UK", "LF", "AF", "OM", "OF", "NPFL", "FKF", "RSSSF"} and provider not in providers:
            providers.append(provider)

    # API-Football is an independent fallback only when both its key and
    # competition league ID are configured. It never replaces the primary.
    if AF_KEY and (competition_code in APIFOOTBALL_LEAGUE_IDS or competition_code in APIFOOTBALL_LEAGUE_SEARCH) and "AF" not in providers:
        providers.append("AF")
    # The public open-matchday feed is last so existing provider data wins.
    if supports_open_matchday(competition_code) and "OM" not in providers:
        providers.append("OM")
    return providers


def _team_key(value):
    value = unicodedata.normalize("NFKD", (value or "").casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("ß", "ss").replace("&", " and ")
    value = re.sub(r"\b(fc|cf|ac|sc|afc|club|de|da|del|ssc|fk|sk|sv|cd|ud)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = " ".join(value.split())
    aliases = {
        "man city": "manchester city",
        "man utd": "manchester united",
        "manchester utd": "manchester united",
        "brighton and hove albion": "brighton",
        "brighton hove albion": "brighton",
        "spurs": "tottenham",
        "tottenham hotspur": "tottenham",
        "celta vigo": "celta",
        "athletic bilbao": "athletic",
        "athletic club": "athletic",
        "barca": "barcelona",
        "atleti": "atletico madrid",
        "atletico": "atletico madrid",
        "inter": "inter milan",
        "internazionale": "inter milan",
        "fc internazionale": "inter milan",
        "psg": "paris saint germain",
        "paris sg": "paris saint germain",
        "paris saint germain": "paris saint germain",
        "bayern munchen": "bayern munich",
        "bayern": "bayern munich",
        "real betis": "betis",
        "sporting lisbon": "sporting cp",
        "sporting clube de portugal": "sporting cp",
        "sporting clube portugal": "sporting cp",
        "sporting portugal": "sporting cp",
    }
    return aliases.get(value, value)


def _fixture_key(match):
    home = (match.get("homeTeam") or {}).get("name")
    away = (match.get("awayTeam") or {}).get("name")
    raw_kickoff = match.get("utcDate") or ""
    if not home or not away or not raw_kickoff:
        return None

    # Different feeds can encode the same kickoff with different UTC offsets.
    # Convert offset-aware timestamps to a common UTC calendar date before
    # comparing fixtures; retain the source date for date-only/naive values.
    match_day = raw_kickoff[:10]
    try:
        kickoff = datetime.fromisoformat(raw_kickoff.replace("Z", "+00:00"))
        if kickoff.tzinfo is not None:
            match_day = kickoff.astimezone(datetime_timezone.utc).date().isoformat()
        else:
            match_day = kickoff.date().isoformat()
    except (TypeError, ValueError):
        return None

    return match_day, _team_key(home), _team_key(away)


def _matching_fixture_key(merged, match):
    """Find an unambiguous same-team fixture when providers differ by one date."""
    key = _fixture_key(match)
    if key is None or key in merged:
        return key

    try:
        match_day = date.fromisoformat(key[0])
    except (TypeError, ValueError):
        return key

    nearby = []
    for candidate in merged:
        if candidate[1:] != key[1:]:
            continue
        try:
            candidate_day = date.fromisoformat(candidate[0])
        except (TypeError, ValueError):
            continue
        if abs((candidate_day - match_day).days) <= 1:
            nearby.append(candidate)

    # If more than one possible match exists, keep it separate rather than
    # risking a result being attached to the wrong fixture.
    return nearby[0] if len(nearby) == 1 else key


def _has_result(match):
    score = (match.get("score") or {}).get("fullTime") or {}
    return score.get("home") is not None and score.get("away") is not None


def _dispatch_explicit_provider(provider, competition_code, match_date):
    """Call one named provider without changing the competition history source."""
    from . import providers

    handler = providers._PROVIDER_DISPATCH.get(provider, {}).get("fetch_matches_by_date")
    if handler is None:
        logger.warning("No fixture handler registered for provider %s", provider)
        return []
    return handler(competition_code, match_date)


def fetch_upcoming_fixture_groups(api_key, competition_code, days_ahead=45):
    """
    Discover upcoming fixtures in bulk where a season feed is available.

    Season CSV providers are queried once, rather than being called once for
    every calendar day. Only the dates with scheduled matches are then passed
    through the regular provider chain to merge and deduplicate sources.
    """
    from datetime import date, timedelta
    from . import providers

    today = date.today()
    # Prediction generation is intentionally capped at seven calendar days.
    # Keep the clamp here as a final guard for every caller.
    bounded_days = min(7, max(1, int(days_ahead)))
    last_day = today + timedelta(days=bounded_days - 1)
    primary = get_provider(competition_code)
    bulk_provider = None
    season_matches = []

    try:
        # Bulk discovery must respect the configured primary source. A
        # secondary season feed is not allowed to add another set of dates
        # when the primary already has coverage; per-date fallback below is
        # reserved for dates where the primary has no usable fixtures.
        if primary == "OM" and supports_open_matchday(competition_code):
            bulk_provider = "OM"
            season_matches = providers._om_current_matches(competition_code)
        elif primary == "UK" and competition_code in providers.FOOTBALL_DATA_UK_CODES:
            bulk_provider = "UK"
            season_matches = providers._uk_current_matches(competition_code)
        elif primary == "LF" and competition_code in LIVEFOOTBALL_LEAGUE_IDS:
            bulk_provider = "LF"
            season_matches = providers._lf_season_matches(competition_code)
        elif primary == "OF" and supports_openfootball(competition_code):
            bulk_provider = "OF"
            season_matches = providers.of_current_matches(competition_code)
        elif primary == "NPFL" and providers.supports_npfl(competition_code):
            bulk_provider = "NPFL"
            from .npfl import current_matches
            season_matches = current_matches(competition_code)

        if bulk_provider:
            candidate_dates = sorted({
                (match.get("utcDate") or "")[:10]
                for match in season_matches
                if match.get("status") not in {"FINISHED", "CANCELLED", "POSTPONED"}
                and (match.get("utcDate") or "")[:10]
                and today.isoformat() <= (match.get("utcDate") or "")[:10] <= last_day.isoformat()
                and _fixture_key(match) is not None
            })
            candidate_count = sum(
                1 for match in season_matches
                if match.get("status") not in {"FINISHED", "CANCELLED", "POSTPONED"}
                and (match.get("utcDate") or "")[:10] in candidate_dates
                and _fixture_key(match) is not None
            )
            record_provider_diagnostic(
                competition_code, bulk_provider,
                "fixtures_found" if candidate_count else "empty_response",
                fixture_count=candidate_count,
                match_date=f"{today.isoformat()}..{last_day.isoformat()}",
            )
            grouped = {}
            for match_day in candidate_dates:
                merged = fetch_fixture_matches_by_date(api_key, competition_code, match_day)
                if merged:
                    grouped[match_day] = merged
            if grouped:
                return grouped
    except Exception as exc:
        if bulk_provider:
            record_provider_diagnostic(
                competition_code, bulk_provider, "error",
                error=exc, match_date=f"{today.isoformat()}..{last_day.isoformat()}",
            )
        logger.exception("Bulk fixture discovery failed for %s", competition_code)

    # Fallback for providers without a season feed, or when the bulk feed has
    # no published fixtures in the requested window.
    from .utils import find_upcoming_match_dates
    dates = find_upcoming_match_dates(
        fetch_fixture_matches_by_date, api_key, competition_code,
        start_date=today, days_ahead=bounded_days,
    )
    grouped = {}
    for match_day in dates:
        matches = fetch_fixture_matches_by_date(api_key, competition_code, match_day)
        if matches:
            grouped[match_day] = matches
    return grouped


def fetch_fixture_matches_by_date(api_key, competition_code, match_date):
    """
    Fetch fixtures from the configured provider chain and merge duplicates.

    api_key remains in the signature for compatibility with the existing date
    helpers; credentials are owned by each provider.
    """
    merged = {}
    for provider in _provider_order(competition_code):
        configured, credential_name = provider_configuration(provider, competition_code)
        if not configured:
            reason = f"{credential_name} is missing, disabled, or not mapped"
            record_provider_diagnostic(
                competition_code, provider, "not_configured",
                error=reason, match_date=match_date,
            )
            logger.warning("Fixture provider %s not configured for %s: %s", provider, competition_code, reason)
            continue

        provider_started = time.monotonic()
        try:
            matches = _dispatch_explicit_provider(provider, competition_code, match_date)
        except Exception as exc:
            logger.info(
                "FIXTURE_PROVIDER_TIMING competition=%s date=%s provider=%s elapsed_seconds=%.3f status=error fixtures=0",
                competition_code, match_date, provider, time.monotonic() - provider_started,
            )
            record_provider_diagnostic(
                competition_code, provider, "error",
                error=exc, match_date=match_date,
            )
            logger.exception(
                "Fixture provider %s failed for %s on %s",
                provider, competition_code, match_date,
            )
            continue

        valid_count = sum(1 for match in (matches or []) if _fixture_key(match) is not None)
        logger.info(
            "FIXTURE_PROVIDER_TIMING competition=%s date=%s provider=%s elapsed_seconds=%.3f status=ok fixtures=%s",
            competition_code, match_date, provider, time.monotonic() - provider_started, valid_count,
        )
        health_cache_key = (
            "provider_request_health::AF::fixtures" if provider == "AF"
            else "provider_request_health::LF" if provider == "LF"
            else None
        )
        request_health = _diagnostic_cache_get(health_cache_key, {}) if health_cache_key else {}
        recent_request_error = False
        if (
            request_health.get("status") in {"error", "not_configured"}
            and request_health.get("checked_at")
            and (provider != "AF" or request_health.get("endpoint") == "fixtures")
        ):
            try:
                checked_at = timezone.datetime.fromisoformat(request_health["checked_at"])
                recent_request_error = (timezone.now() - checked_at).total_seconds() < 90
            except (TypeError, ValueError):
                recent_request_error = False
        if not valid_count and recent_request_error:
            provider_label = "API-Football" if provider == "AF" else "Live-Football-Data"
            record_provider_diagnostic(
                competition_code, provider, "error",
                error=request_health.get("last_error") or f"{provider_label} request failed",
                match_date=match_date,
            )
        else:
            diagnostic_error = None
            if provider == "OF" and not valid_count:
                # OpenFootball often has useful historical seasons but does
                # not publish the current African season. Explain that gap
                # instead of making an empty current-season file look like a
                # transient fixture-fetch failure.
                try:
                    from .openfootball import current_season_year, _latest_available_season_year
                    requested_season = current_season_year(competition_code)
                    latest_season = _latest_available_season_year(competition_code)
                    if latest_season is None:
                        diagnostic_error = "No season file is published for this competition."
                    elif latest_season < requested_season:
                        diagnostic_error = (
                            f"No OpenFootball file for current season {requested_season}; "
                            f"latest published season starts {latest_season}."
                        )
                except Exception as exc:
                    logger.debug("Could not inspect OpenFootball season coverage for %s: %s", competition_code, exc)
            record_provider_diagnostic(
                competition_code, provider,
                "fixtures_found" if valid_count else "empty_response",
                fixture_count=valid_count, error=diagnostic_error, match_date=match_date,
            )

        for match in matches or []:
            key = _matching_fixture_key(merged, match)
            if key is None:
                continue
            existing = merged.get(key)
            if existing is None or (_has_result(match) and not _has_result(existing)):
                merged[key] = match

        # Provider order is a fallback chain, not an aggregation list. Once
        # the current source supplies any valid fixture for this date, stop:
        # this avoids duplicate requests and conflicting provider snapshots.
        if valid_count:
            break

    return sorted(
        merged.values(),
        key=lambda item: (item.get("utcDate") or "", (item.get("homeTeam") or {}).get("name") or ""),
    )
