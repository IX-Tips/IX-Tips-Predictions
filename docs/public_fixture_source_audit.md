# Public fixture-source audit

This command is a conservative, read-only discovery step—not a live fixture provider.

Run:
\`\`\`
python manage.py audit_public_fixture_sources
python manage.py audit_public_fixture_sources --code NGA
python manage.py audit_public_fixture_sources --code KEN
\`\`\`

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

The experimental parser in \`predict/rsssf_audit_parser.py\` handles RSSSF-style
round headings, month-name date blocks (for example, [Oct 10]), numeric dates,
and scheduled or completed fixture rows. It rolls season dates into the next
calendar year when the month is before the configured season start month.
Undated/TBA rows are ignored, and exact date/home/away duplicates are removed.

Tests use a short representative text sample matching the visible 2026/27
RSSSF page layout. This verifies the parser's text-shape handling only; it is
not a full live-page extraction test, source permission check, or production
fixture validation. The parser remains isolated from ingestion and prediction
generation. RSSSF's page requests acknowledgement when copying; any reuse must
retain attribution. The audit command must also pass its robots check before
the page is fetched by that command.
