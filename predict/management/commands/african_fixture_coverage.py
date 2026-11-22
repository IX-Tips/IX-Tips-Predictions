"""Read-only diagnostic report for African fixture coverage."""
import json
from datetime import date

from django.core.management.base import BaseCommand
from django.db.models import Count

from predict.constants import COMPETITIONS, COMPETITION_PROVIDERS, OPENFOOTBALL_COMPETITIONS
from predict.fixture_sources import (
    _provider_order, get_provider, get_provider_diagnostic, provider_configuration,
)
from predict.models import MatchPrediction

AFRICAN_CODES = set(OPENFOOTBALL_COMPETITIONS)


def build_report(today=None):
    today = today or date.today()
    codes = sorted(AFRICAN_CODES)
    counts = dict(
        MatchPrediction.objects.filter(match_date__gte=today)
        .exclude(status__in=("FINISHED", "CANCELLED", "POSTPONED"))
        .values("competition").annotate(total=Count("id"))
        .values_list("competition", "total")
    )
    rows = []
    for code in codes:
        primary = get_provider(code)
        chain = _provider_order(code)
        chain = list(dict.fromkeys([primary, *chain]))
        providers = []
        for provider in chain:
            configured, config_label = provider_configuration(provider, code)
            diagnostic = get_provider_diagnostic(code, provider) or {}
            providers.append({
                "provider": provider,
                "configured": bool(configured),
                "configuration_label": config_label,
                "diagnostic": diagnostic or None,
            })
        rows.append({
            "code": code,
            "competition": COMPETITIONS.get(code, code),
            "primary": primary,
            "provider_chain": providers,
            "upcoming_prediction_records": int(counts.get(code, 0)),
        })
    return {
        "generated_for_date": today.isoformat(),
        "note": ("Counts are saved upcoming MatchPrediction records, not raw provider fixtures. "
                 "Diagnostics reflect cached attempts only and do not prove fixture coverage."),
        "competitions": rows,
    }


class Command(BaseCommand):
    help = "Report African fixture-provider configuration and saved upcoming prediction-record counts (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Print machine-readable JSON.")

    def handle(self, *args, **options):
        report = build_report()
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
            return
        self.stdout.write(report["note"])
        self.stdout.write(f"Date: {report['generated_for_date']}")
        for row in report["competitions"]:
            statuses = []
            for item in row["provider_chain"]:
                diag = item["diagnostic"] or {}
                state = diag.get("status", "no cached diagnostic")
                stamp = diag.get("last_checked", "")
                statuses.append(f"{item['provider']}={'configured' if item['configured'] else 'disabled'}; {state}" + (f" ({stamp})" if stamp else ""))
            self.stdout.write(
                f"{row['code']} | {row['competition']} | primary={row['primary']} | "
                f"upcoming prediction records={row['upcoming_prediction_records']} | "
                + " || ".join(statuses)
            )
