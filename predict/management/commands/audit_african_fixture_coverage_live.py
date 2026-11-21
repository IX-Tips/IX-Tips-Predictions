"""Opt-in live API-Football coverage audit for African competitions.

Unlike audit_african_fixture_coverage, this command makes provider requests.
It does not write fixtures, generate predictions, or modify competition data.
"""
from datetime import date

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from predict.constants import COMPETITIONS, APIFOOTBALL_LEAGUE_SEARCH
from predict.providers import AF_KEY, af_league_id, af_season, _af_get

AFRICAN_CODES = (
    "ALG", "EGY", "GHA", "KEN", "MWI", "MAR", "NGA",
    "RWA", "SEN", "TZA", "UGA", "ZMB", "ZWE",
)


class Command(BaseCommand):
    help = (
        "Opt-in live API-Football audit for African leagues. Makes league and "
        "fixture requests but does not persist fixtures or generate predictions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--code", action="append", dest="codes",
            help="Audit only this competition code (repeatable). Defaults to all mapped African leagues.",
        )

    def handle(self, *args, **options):
        if not AF_KEY:
            raise CommandError("API-Football is not configured (APIFOOTBALL_KEY/APIFOOTBALL_API_KEY).")

        codes = [str(code).upper() for code in (options.get("codes") or AFRICAN_CODES)]
        unknown = [code for code in codes if code not in COMPETITIONS or code not in APIFOOTBALL_LEAGUE_SEARCH]
        if unknown:
            raise CommandError(
                "Unsupported/unmapped African competition code(s): " + ", ".join(unknown)
            )

        self.stdout.write(
            self.style.WARNING(
                "LIVE AUDIT: makes API-Football requests; no fixtures or predictions are saved."
            )
        )
        for code in codes:
            season = af_season(code, date.today())
            self.stdout.write(f"\n{code} | {COMPETITIONS[code]} | season={season}")

            league_id = af_league_id(code, season)
            lookup_health = self._health("leagues")
            if not league_id:
                if lookup_health.get("status") == "error":
                    state = "LEAGUE_LOOKUP_ERROR"
                elif lookup_health.get("status") == "not_configured":
                    state = "API_NOT_CONFIGURED"
                else:
                    state = "NO_EXACT_LEAGUE_MATCH"
                self.stdout.write(
                    f"  {state}; league_id=unresolved; API health="
                    f"{lookup_health.get('status', 'unknown')}; "
                    f"detail={lookup_health.get('last_error') or 'No exact country/name match'}"
                )
                continue

            fixtures = _af_get("fixtures", {"league": league_id, "season": season})
            fixture_health = self._health("fixtures")
            if fixture_health.get("status") == "error":
                state = "FIXTURE_REQUEST_ERROR"
            elif fixture_health.get("status") == "not_configured":
                state = "API_NOT_CONFIGURED"
            elif fixtures:
                state = "FIXTURES_RETURNED"
            else:
                state = "EMPTY_CURRENT_SEASON"
            self.stdout.write(
                f"  {state}; league_id={league_id}; raw_fixtures={len(fixtures)}; "
                f"API health={fixture_health.get('status', 'unknown')}; "
                f"detail={fixture_health.get('last_error') or '-'}"
            )

    @staticmethod
    def _health(endpoint):
        try:
            value = cache.get(f"provider_request_health::AF::{endpoint}")
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
