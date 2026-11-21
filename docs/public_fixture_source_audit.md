# Public fixture-source audit

This command is a conservative, read-only discovery step—not a live fixture provider.

Run:
```
python manage.py audit_public_fixture_sources
python manage.py audit_public_fixture_sources --code NGA
```

It checks a curated source's robots.txt before fetching the configured page,
uses a descriptive user-agent, timeout limits, and does not save fixtures or
predictions. It reports response status and JSON-LD hints only; it does not
claim extracted fixtures are valid or permission to reuse them.

Do not configure a source with explicit anti-automation restrictions or bypass
access controls. Review terms, robots policy, and reuse obligations before
building a production adapter. Sources are deliberately curated, not discovered
by crawling.
