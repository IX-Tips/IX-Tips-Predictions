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

from django.core.cache import cache
from django.utils import timezone

from .providers import (
    dispatch_provider, get_provider, AF_KEY, LF_KEY, API_TOKEN,
    supports_open_matchday, LIVEFOOTBALL_LEAGUE_IDS,
)
from .constants import APIFOOTBALL_LEAGUE_IDS

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
    return False, "Unknown provider"


def record_provider_diagnostic(competition_code, provider, status, fixture_count=0, error=None, match_date=None):
    """Persist the latest fixture-provider attempt for the staff diagnostics page."""
    key = provider_diagnostic_key(competition_code, provider)
    previous = cache.get(key, {}) or {}
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
    cache.set(key, state, timeout=PROVIDER_DIAGNOSTIC_TTL)
    return state


def get_provider_diagnostic(competition_code, provider):
    return cache.get(provider_diagnostic_key(competition_code, provider))


def _provider_order(competition_code):
    configured = FIXTURE_PROVIDER_ORDER.get(competition_code)
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, (list, tuple)):
        configured = [get_provider(competition_code)]

    providers = []
    for provider in configured:
        provider = str(provider).strip().upper()
        if provider in {"FD", "UK", "LF", "AF", "OM"} and provider not in providers:
            providers.append(provider)

    # API-Football is an independent fallback only when both its key and
    # competition league ID are configured. It never replaces the primary.
    if AF_KEY and competition_code in APIFOOTBALL_LEAGUE_IDS and "AF" not in providers:
        providers.append("AF")
    # The public open-matchday feed is last so existing provider data wins.
    if supports_open_matchday(competition_code) and "OM" not in providers:
        providers.append("OM")
    return providers


def _team_key(value):
    value = (value or "").casefold()
    value = re.sub(r"\b(fc|cf|ac|sc|afc|club)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _fixture_key(match):
    home = (match.get("homeTeam") or {}).get("name")
    away = (match.get("awayTeam") or {}).get("name")
    match_day = (match.get("utcDate") or "")[:10]
    if not home or not away or not match_day:
        return None
    # Provider feeds may express the same kickoff in different time zones.
    # A team pair can only meet once in a competition on a given match day.
    return match_day, _team_key(home), _team_key(away)


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

        try:
            matches = _dispatch_explicit_provider(provider, competition_code, match_date)
        except Exception as exc:
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
        record_provider_diagnostic(
            competition_code, provider,
            "fixtures_found" if valid_count else "empty_response",
            fixture_count=valid_count, match_date=match_date,
        )

        for match in matches or []:
            key = _fixture_key(match)
            if key is None:
                continue
            existing = merged.get(key)
            if existing is None or (_has_result(match) and not _has_result(existing)):
                merged[key] = match

    return sorted(
        merged.values(),
        key=lambda item: (item.get("utcDate") or "", (item.get("homeTeam") or {}).get("name") or ""),
    )
