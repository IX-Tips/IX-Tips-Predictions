"""Audit club badge resolution for teams present in saved predictions.

Run with: python manage.py audit_team_logos
Optional remote checks: python manage.py audit_team_logos --check-urls
"""
import csv
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand

from predict.constants import (
    country_flag_url,
    get_football_team_logo_url,
)
from predict.models import MatchPrediction


class Command(BaseCommand):
    help = "Report missing club badges and optionally verify resolved image URLs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check-urls",
            action="store_true",
            help="Send bounded HEAD requests to check resolved club-logo URLs.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=4.0,
            help="Per-image URL check timeout in seconds (default: 4).",
        )

    def _url_status(self, url, timeout):
        if not url:
            return "missing"
        request = Request(url, method="HEAD", headers={"User-Agent": "IX-Tips-logo-audit/1.0"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return "ok" if 200 <= response.status < 400 else f"http_{response.status}"
        except HTTPError as exc:
            return f"http_{exc.code}"
        except (URLError, TimeoutError, OSError) as exc:
            return f"unreachable:{type(exc).__name__}"

    def handle(self, *args, **options):
        check_urls = options["check_urls"]
        timeout = max(1.0, min(float(options["timeout"]), 10.0))
        teams = (
            MatchPrediction.objects.order_by()
            .values_list("competition", "home_team")
            .distinct()
        )
        away_teams = (
            MatchPrediction.objects.order_by()
            .values_list("competition", "away_team")
            .distinct()
        )
        unique = sorted({
            (str(code or ""), str(name or "").strip())
            for code, name in list(teams) + list(away_teams)
            if name and name.strip()
        })

        writer = csv.writer(self.stdout)
        writer.writerow([
            "competition", "team", "club_logo_url", "flag_fallback_url",
            "resolution", "url_status",
        ])
        counts = {"club_logo": 0, "flag_fallback": 0, "missing": 0, "broken": 0}

        for competition, team in unique:
            logo_url = get_football_team_logo_url(team, competition)
            flag_url = country_flag_url(competition)
            if logo_url:
                resolution = "club_logo"
                counts["club_logo"] += 1
                url_status = self._url_status(logo_url, timeout) if check_urls else "not_checked"
                if check_urls and url_status != "ok":
                    counts["broken"] += 1
            elif flag_url:
                resolution = "flag_fallback"
                counts["flag_fallback"] += 1
                url_status = "not_checked"
            else:
                resolution = "missing"
                counts["missing"] += 1
                url_status = "missing"

            writer.writerow([
                competition, team, logo_url or "", flag_url or "",
                resolution, url_status,
            ])

        self.stderr.write(
            self.style.SUCCESS(
                "LOGO_AUDIT "
                + " ".join(f"{key}={value}" for key, value in counts.items())
                + f" teams={len(unique)} remote_checks={str(check_urls).lower()}"
            )
        )
