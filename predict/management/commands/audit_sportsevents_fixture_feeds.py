"""Compare read-only SportsEvents24 fixture feeds for audited leagues.

No database writes are performed. The command requests only the provider's
read-only /events endpoint and prints a compact sample for each tournament.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from predict.constants import COMPETITIONS
from predict.external_fixture_providers import (
    SE_BASE_URL, _SE_COUNTRIES, _SE_NAME_ALIASES, _get_json, _norm,
    _se_tournaments,
)


def _row_name(row):
    value = row.get("name") or row.get("tournament") or row.get("title")
    if isinstance(value, dict):
        value = value.get("name") or value.get("title")
    return str(value or "").strip()


def _row_id(row):
    return row.get("id") or row.get("tournamentId")


def _candidates(code, rows):
    aliases = _SE_NAME_ALIASES.get(code) or (COMPETITIONS[code],)
    wanted = {_norm(alias) for alias in aliases if alias}
    return [
        row for row in rows
        if _norm(_row_name(row)) in wanted and _row_id(row)
    ]


class Command(BaseCommand):
    help = "Compare SportsEvents24 tournament fixture feeds without database writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--code", action="append", dest="codes",
            help="Competition code to inspect; repeatable. Defaults to mapped leagues plus South Africa.",
        )
        parser.add_argument(
            "--days", type=int, default=7,
            help="Forward date window (1-30 days; default: 7).",
        )

    def handle(self, *args, **options):
        codes = [c.upper() for c in (options.get("codes") or [
            "EGY", "GHA", "MAR", "MTN", "MWI", "SEN", "TZA", "ZAF"
        ])]
        unknown = [c for c in codes if c not in _SE_COUNTRIES or c not in COMPETITIONS]
        if unknown:
            raise CommandError("Unsupported SportsEvents24 codes: " + ", ".join(unknown))
        days = options["days"]
        if not 1 <= days <= 30:
            raise CommandError("--days must be between 1 and 30.")

        start = date.today()
        end = start + timedelta(days=days)
        self.stdout.write(self.style.WARNING(
            f"READ-ONLY FIXTURE AUDIT: /events only; {start} through {end}; "
            "no database writes."
        ))

        for code in codes:
            try:
                catalog = _se_tournaments(_SE_COUNTRIES[code])
            except Exception as exc:
                self.stdout.write(f"{code} | CATALOGUE_ERROR | {str(exc)[:220]}")
                continue

            candidates = _candidates(code, catalog)
            if not candidates:
                self.stdout.write(f"{code} | NO_EXACT_TOURNAMENT_MATCH")
                continue

            for tournament in candidates:
                tournament_id = _row_id(tournament)
                name = _row_name(tournament)
                try:
                    payload = _get_json(f"{SE_BASE_URL}/events", params={
                        "tournament_id": tournament_id,
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "limit": 100,
                    })
                    events = payload.get("data")
                    if not isinstance(events, list):
                        raise ValueError("events response has no data list")
                except Exception as exc:
                    self.stdout.write(
                        f"{code} | id={tournament_id} | {name} | EVENTS_ERROR | {str(exc)[:220]}"
                    )
                    continue

                self.stdout.write(
                    f"{code} | id={tournament_id} | {name} | events={len(events)}"
                )
                for event in events[:5]:
                    home = event.get("teamHome") or "?"
                    away = event.get("teamAway") or "?"
                    if isinstance(home, dict):
                        home = home.get("name") or "?"
                    if isinstance(away, dict):
                        away = away.get("name") or "?"
                    self.stdout.write(
                        f"  - {event.get('startsAt') or 'NO_DATE'} | {home} vs {away}"
                    )
