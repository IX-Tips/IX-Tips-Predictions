"""Read-only cached African fixture-provider coverage audit (no provider requests)."""
from datetime import datetime
from django.core.management.base import BaseCommand
from django.core.cache import cache

from predict.constants import COMPETITIONS, APIFOOTBALL_LEAGUE_SEARCH
from predict.fixture_sources import get_provider_diagnostic, _provider_order, provider_configuration

AFRICAN_CODES = ("ALG", "EGY", "GHA", "KEN", "MWI", "MAR", "NGA", "RWA", "SEN", "ZAF", "TZA", "UGA", "ZMB", "ZWE")


class Command(BaseCommand):
    help = "Print cached fixture-source diagnostics for African competitions; does not call providers."

    def handle(self, *args, **options):
        self.stdout.write("African fixture coverage (cached diagnostics only; no API calls)")
        self.stdout.write("=" * 100)
        for code in AFRICAN_CODES:
            if code not in COMPETITIONS:
                continue
            providers = _provider_order(code)
            self.stdout.write(f"\n{code} | {COMPETITIONS[code]} | order: {' -> '.join(providers)}")
            for provider in providers:
                configured, label = provider_configuration(provider, code)
                diag = get_provider_diagnostic(code, provider) or {}
                status = diag.get("status", "never_checked")
                checked = diag.get("last_checked") or "-"
                count = diag.get("fixture_count", 0)
                err = diag.get("last_error") or ""
                self.stdout.write(
                    f"  {provider}: {'configured' if configured else 'not configured'} "
                    f"({label}); status={status}; count={count}; checked={checked}"
                )
                if err:
                    self.stdout.write(f"    note: {err[:240]}")
            if code in APIFOOTBALL_LEAGUE_SEARCH:
                try:
                    health = cache.get("provider_request_health::AF::fixtures")
                except Exception:
                    health = None
                if health:
                    self.stdout.write(
                        f"  AF fixtures endpoint health: {health.get('status', '?')} "
                        f"at {health.get('checked_at', '-')}; {health.get('last_error') or ''}"
                    )
