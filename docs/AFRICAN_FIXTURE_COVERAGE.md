# African fixture coverage report

Run the read-only diagnostic:

```bash
python manage.py african_fixture_coverage
python manage.py african_fixture_coverage --json
```

The report lists configured provider chains, configuration state, cached diagnostics and counts of saved upcoming **MatchPrediction records** by African competition.

These record counts are not raw-provider fixture counts. Provider diagnostics are cached request history and may be stale or absent; they do not independently verify source coverage. The command does not contact providers, write fixture data, or generate predictions.
