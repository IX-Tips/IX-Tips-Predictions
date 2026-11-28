"""Read-only Football Charts catalogue-to-competition mapping audit.

This command makes one catalogue request (served from the existing cache when
available). It does not call per-league fixture endpoints, consume the daily
fixture quota, persist fixtures, or generate predictions.
"""
from django.core.management.base import BaseCommand, CommandError

from predict.constants import COMPETITIONS
from predict.external_fixture_providers import (
    FC_KEY,
    _FC_COUNTRIES,
    _FC_NAME_ALIASES,
    _FC_SLUG_OVERRIDES,
    _fc_catalog,
    _fc_league_slug,
    _norm,
)


class Command(BaseCommand):
    help = (
        "Compare every internal competition against the Football Charts "
        "catalogue using one cached catalogue request only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--african-only",
            action="store_true",
            help="Limit output to competitions with an African country mapping.",
        )
        parser.add_argument(
            "--show-catalogue",
            action="store_true",
            help="Also print every Football Charts catalogue entry.",
        )

    def handle(self, *args, **options):
        if not FC_KEY:
            raise CommandError(
                "Football Charts is not configured "
                "(FOOTBALL_CHARTS_API_KEY/FC_API_KEY)."
            )

        try:
            catalogue = _fc_catalog()
        except Exception as exc:
            raise CommandError(f"Could not load Football Charts catalogue: {exc}") from exc

        if not isinstance(catalogue, list):
            raise CommandError("Football Charts catalogue is not a list.")

        codes = sorted(COMPETITIONS)
        if options["african_only"]:
            african_codes = set(_FC_COUNTRIES) | set(_FC_SLUG_OVERRIDES)
            codes = [code for code in codes if code in african_codes]

        claimed_slugs = set()
        mapped = unmapped = ambiguous = 0
        self.stdout.write(
            self.style.WARNING(
                f"READ-ONLY CATALOGUE AUDIT: {len(catalogue)} provider leagues; "
                "one catalogue request at most; no fixture endpoints called."
            )
        )
        self.stdout.write("CODE | INTERNAL COMPETITION | RESULT | CANDIDATES")

        for code in codes:
            slug = _fc_league_slug(code)
            if slug:
                mapped += 1
                claimed_slugs.add(str(slug))
                result = "MAPPED"
                candidates = [slug]
            else:
                wanted = _norm(_FC_NAME_ALIASES.get(code) or COMPETITIONS[code])
                country = _norm(_FC_COUNTRIES.get(code))
                candidates = [
                    str(row.get("league"))
                    for row in catalogue
                    if _norm(row.get("name")) == wanted
                    and (not country or _norm(row.get("country")) == country)
                    and row.get("league")
                ]
                if len(candidates) > 1:
                    ambiguous += 1
                    result = "AMBIGUOUS"
                else:
                    unmapped += 1
                    result = "NO_EXACT_MATCH"
            self.stdout.write(
                f"{code} | {COMPETITIONS[code]} | {result} | "
                f"{', '.join(candidates) if candidates else '-'}"
            )

        unclaimed = [
            row for row in catalogue
            if row.get("league") and str(row.get("league")) not in claimed_slugs
        ]
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"SUMMARY: mapped={mapped}; unmapped={unmapped}; "
                f"ambiguous={ambiguous}; catalogue_unclaimed={len(unclaimed)}"
            )
        )
        if options["show_catalogue"]:
            self.stdout.write("\nUNCLAIMED CATALOGUE ENTRIES")
            for row in unclaimed:
                self.stdout.write(
                    f"{row.get('league')} | {row.get('name')} | {row.get('country')}"
                )
