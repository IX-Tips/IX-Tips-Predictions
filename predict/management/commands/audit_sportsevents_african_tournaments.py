"""Read-only SportsEvents24 tournament catalogue audit for African leagues.

This command fetches country tournament catalogues only. It does not call event
or fixture endpoints, persist data, or generate predictions.
"""
from django.core.management.base import BaseCommand, CommandError

from predict.constants import COMPETITIONS
from predict.external_fixture_providers import (
    _SE_COUNTRIES,
    _SE_NAME_ALIASES,
    _norm,
    _se_tournaments,
)


def _tournament_name(row):
    value = (
        row.get("name") or row.get("tournament") or row.get("title")
        or row.get("tournamentName") or row.get("tournament_name")
    )
    if isinstance(value, dict):
        value = value.get("name") or value.get("title")
    return str(value or "").strip()


def _tournament_id(row):
    return row.get("id") or row.get("tournamentId")


class Command(BaseCommand):
    help = (
        "Audit SportsEvents24 African tournament catalogues without requesting "
        "fixtures or events."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--code", action="append", dest="codes",
            help="Audit one competition code (repeatable); defaults to all configured SportsEvents24 African codes.",
        )
        parser.add_argument(
            "--show-tournaments", action="store_true",
            help="Print every tournament returned for each audited country.",
        )

    def handle(self, *args, **options):
        codes = [str(code).upper() for code in (options.get("codes") or sorted(_SE_COUNTRIES))]
        unknown = [code for code in codes if code not in _SE_COUNTRIES or code not in COMPETITIONS]
        if unknown:
            raise CommandError(
                "No SportsEvents24 country mapping for: " + ", ".join(unknown)
            )

        self.stdout.write(self.style.WARNING(
            "READ-ONLY TOURNAMENT AUDIT: country catalogues only; "
            "no /events or fixture endpoints called."
        ))
        mapped = missing = ambiguous = 0
        for code in codes:
            country = _SE_COUNTRIES[code]
            wanted = _norm(_SE_NAME_ALIASES.get(code) or COMPETITIONS[code])
            try:
                rows = _se_tournaments(country)
            except Exception as exc:
                self.stdout.write(
                    f"{code} | {COMPETITIONS[code]} | CATALOGUE_ERROR | {str(exc)[:250]}"
                )
                continue

            candidates = [
                row for row in rows
                if _norm(_tournament_name(row)) == wanted and _tournament_id(row)
            ]
            if len(candidates) == 1:
                mapped += 1
                result = f"MAPPED | id={_tournament_id(candidates[0])} | {_tournament_name(candidates[0])}"
            elif len(candidates) > 1:
                ambiguous += 1
                result = "AMBIGUOUS | " + "; ".join(
                    f"id={_tournament_id(row)} name={_tournament_name(row)}"
                    for row in candidates
                )
            else:
                missing += 1
                result = f"NO_EXACT_MATCH | catalogue_entries={len(rows)}"
            self.stdout.write(f"{code} | {COMPETITIONS[code]} | {result}")

            if options["show_tournaments"]:
                for row in rows:
                    self.stdout.write(
                        f"  - id={_tournament_id(row)} | {_tournament_name(row)}"
                    )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"SUMMARY: mapped={mapped}; no_exact_match={missing}; ambiguous={ambiguous}; "
            f"audited={len(codes)}"
        ))
        self.stdout.write(
            "NOTE: Kenya, Uganda and Nigeria are not included in the default "
            "SportsEvents24 mapping; their existing FKF, OpenFootball and NPFL "
            "routes remain untouched."
        )
