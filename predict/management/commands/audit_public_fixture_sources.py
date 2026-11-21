"""Conservative public-source probe for potential African fixture sources.

Diagnostic-only: no fixture persistence or prediction generation. Fetch robots.txt
first and fail closed. Source terms should be reviewed separately.
"""
import json
import re
from urllib.robotparser import RobotFileParser

import requests
from django.core.management.base import BaseCommand

from predict.rsssf_audit_parser import extract_rsssf_text, parse_rsssf_text

SOURCES = {
    "NGA": {
        "name": "NPFL fixtures and results",
        "url": "https://npfl.com.ng/fixtures-results/",
        "robots_url": "https://npfl.com.ng/robots.txt",
    },
    "KEN": {
        "name": "RSSSF Kenya Premier League 2026/27",
        "url": "https://www.rsssf.org/tablesk/kenya2027.html",
        "robots_url": "https://www.rsssf.org/robots.txt",
    },
}
USER_AGENT = "IXTipsSourceAudit/1.0 (+fixture-source diagnostic)"
JSONLD_RE = re.compile(
    r'<script\b[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>',
    re.I | re.S,
)


class Command(BaseCommand):
    help = "Probe configured public fixture-source pages, robots policy and structured-data hints (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--code", action="append", dest="codes", help="Competition code (repeatable).")

    def handle(self, *args, **options):
        codes = [str(x).upper() for x in (options.get("codes") or SOURCES)]
        for code in codes:
            source = SOURCES.get(code)
            if not source:
                self.stdout.write(f"{code}: NO_SOURCE_CONFIGURED")
                continue
            self.stdout.write(f"\n{code} | {source['name']} | {source['url']}")
            try:
                robots_response = requests.get(source["robots_url"], headers={"User-Agent": USER_AGENT}, timeout=12)
                robots_response.raise_for_status()
                parser = RobotFileParser()
                parser.parse(robots_response.text.splitlines())
                allowed = parser.can_fetch(USER_AGENT, source["url"])
            except Exception as exc:
                self.stdout.write(f"  ROBOTS_CHECK_FAILED: {str(exc)[:300]}; page not fetched")
                continue
            self.stdout.write(f"  robots_status={robots_response.status_code}; allowed={allowed}")
            if not allowed:
                self.stdout.write("  ROBOTS_DISALLOWED; page not fetched")
                continue
            try:
                response = requests.get(source["url"], headers={"User-Agent": USER_AGENT}, timeout=15)
                self.stdout.write(f"  page_status={response.status_code}; content_type={response.headers.get('Content-Type','')}")
                if not response.ok:
                    continue
                html = response.text[:2_000_000]
                jsonld = JSONLD_RE.findall(html)
                self.stdout.write(f"  jsonld_blocks={len(jsonld)}; html_bytes={len(response.text)}; scan_limit_bytes=2000000")
                for index, block in enumerate(jsonld[:5], 1):
                    try:
                        parsed = json.loads(block.strip())
                        types = parsed.get("@type") if isinstance(parsed, dict) else type(parsed).__name__
                        self.stdout.write(f"  jsonld_{index}_type={types}")
                    except (ValueError, TypeError):
                        self.stdout.write(f"  jsonld_{index}=unparseable")
                if code == "KEN":
                    extracted_text = extract_rsssf_text(html)
                    parsed_fixtures = parse_rsssf_text(
                        extracted_text, season_year=2026, season_start_month=7
                    )
                    fixtures = parsed_fixtures["fixtures"]
                    completed = sum(item["status"] == "finished" for item in fixtures)
                    scheduled = sum(item["status"] == "scheduled" for item in fixtures)
                    self.stdout.write(
                        f"  rsssf_lines={parsed_fixtures['lines_scanned']}; "
                        f"fixtures_parsed={len(fixtures)}; completed={completed}; "
                        f"scheduled={scheduled}; skipped={len(parsed_fixtures['skipped'])}"
                    )
                    for fixture in fixtures[:10]:
                        score = fixture["score"]
                        score_text = f"{score[0]}-{score[1]}" if score else "vs"
                        self.stdout.write(
                            f"  fixture={fixture['date']} | {fixture['home']} "
                            f"{score_text} {fixture['away']} | {fixture['status']}"
                        )
                    self.stdout.write(
                        "  NOTE: extracted rows are diagnostic only; no fixtures or "
                        "predictions are saved."
                    )
                self.stdout.write("  NOTE: status/structured-data hints are not fixture validation.")
            except Exception as exc:
                self.stdout.write(f"  PAGE_FETCH_ERROR: {str(exc)[:300]}")
