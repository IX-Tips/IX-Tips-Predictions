"""Conservative public-source probe for potential African fixture sources.

This audit is diagnostic-only: it never saves fixtures or generates predictions.
It fetches robots.txt first and does not probe a page if robots rules disallow
the audit command's user-agent. Source terms should be reviewed separately.
"""
import json
import re
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from django.core.management.base import BaseCommand

SOURCES = {
    "NGA": {
        "name": "NPFL official documents",
        "url": "https://npfl.com.ng/npfl-documents/",
        "robots_url": "https://npfl.com.ng/robots.txt",
    },
}
USER_AGENT = "IXTipsSourceAudit/1.0 (+fixture-source diagnostic)"


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
                jsonld = re.findall(r'<script[^>]+type=["\\']application/ld\\+json["\\'][^>]*>(.*?)</script>', html, re.I | re.S)
                self.stdout.write(f"  jsonld_blocks={len(jsonld)}; html_bytes={len(response.text)}")
                for index, block in enumerate(jsonld[:5], 1):
                    try:
                        parsed = json.loads(block.strip())
                        types = parsed.get("@type") if isinstance(parsed, dict) else type(parsed).__name__
                        self.stdout.write(f"  jsonld_{index}_type={types}")
                    except (ValueError, TypeError):
                        self.stdout.write(f"  jsonld_{index}=unparseable")
                self.stdout.write("  NOTE: status/structured-data hints are not fixture validation.")
            except Exception as exc:
                self.stdout.write(f"  PAGE_FETCH_ERROR: {str(exc)[:300]}")
