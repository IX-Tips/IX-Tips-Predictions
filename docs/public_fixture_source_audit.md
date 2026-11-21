# Public fixture-source audit

This command is a conservative, read-only discovery step—not a live fixture provider.

Run:
```
python manage.py audit_public_fixture_sources
python manage.py audit_public_fixture_sources --code NGA
python manage.py audit_public_fixture_sources --code KEN
```

Configured probes: Nigeria's NPFL fixtures/results page and RSSSF's Kenya 2026/27
league page. The command checks robots.txt before fetching a configured page,
uses a descriptive user-agent and timeout limits, and does not save fixtures or
predictions. It reports response status and JSON-LD hints only; it does not
claim extracted fixtures are valid or permission to reuse them.

The scan is limited to the first 2 MB of HTML for structured-data hints. This
is not a parser or source quality test. Next steps require validating actual
fixture extraction, date/team normalization, duplicates and source coverage.

Do not configure a source with explicit anti-automation restrictions or bypass
access controls. Review terms, robots policy, and reuse obligations before
building a production adapter. Sources are deliberately curated, not discovered
by crawling.


## Parser experiment

A separate experimental parser (`predict/rsssf_audit_parser.py`) can parse a limited
set of clearly dated text lines for audit/testing. It deliberately skips ambiguous
lines and does not fetch pages, persist data, or feed prediction jobs. Automated
fixture-shaped test lines validate basic scheduled/completed distinction and
exact-duplicate handling. Real page extraction quality must be measured before
this parser is used against RSSSF page output.
