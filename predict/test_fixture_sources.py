from unittest import TestCase
from unittest.mock import patch

from predict.fixture_sources import fetch_fixture_matches_by_date


class FixtureAggregationTests(TestCase):
    def setUp(self):
        self.fixture = {
            "id": "uk-1",
            "utcDate": "2026-11-10T15:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Alpha FC", "crest": None},
            "awayTeam": {"name": "Beta", "crest": None},
            "score": {"fullTime": {"home": None, "away": None}},
        }

    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._provider_order", return_value=["UK", "AF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    def test_tries_fallback_and_deduplicates_same_fixture(self, dispatch, provider_order, configuration):
        second = {
            **self.fixture,
            "id": 12345,
            "homeTeam": {"name": "Alpha", "crest": "crest.png"},
        }
        dispatch.side_effect = [[self.fixture], [second]]

        matches = fetch_fixture_matches_by_date(None, "BEL", "2026-11-10")

        self.assertEqual(len(matches), 1)
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(matches[0]["id"], "uk-1")

    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._provider_order", return_value=["UK", "AF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    def test_provider_failure_does_not_block_fallback(self, dispatch, provider_order, configuration):
        dispatch.side_effect = [RuntimeError("provider offline"), [self.fixture]]

        matches = fetch_fixture_matches_by_date(None, "BEL", "2026-11-10")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["homeTeam"]["name"], "Alpha FC")

    @patch("predict.fixture_sources._provider_order", return_value=["UK"])
    @patch("predict.fixture_sources._dispatch_explicit_provider", return_value=[])
    def test_empty_primary_response_returns_empty_list(self, dispatch, provider_order):
        self.assertEqual(fetch_fixture_matches_by_date(None, "BEL", "2026-11-10"), [])


class ProviderDiagnosticsTests(TestCase):
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources.record_provider_diagnostic")
    @patch("predict.fixture_sources._provider_order", return_value=["LF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    def test_records_fixture_count_for_successful_provider(self, dispatch, provider_order, record, configuration):
        dispatch.return_value = [{"utcDate": "2026-11-21T15:00:00Z", "homeTeam": {"name": "Alpha"}, "awayTeam": {"name": "Beta"}}]
        fetch_fixture_matches_by_date(None, "SAU", "2026-11-21")
        record.assert_called_once()
        self.assertEqual(record.call_args.args[2], "fixtures_found")
        self.assertEqual(record.call_args.kwargs["fixture_count"], 1)

    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources.record_provider_diagnostic")
    @patch("predict.fixture_sources._provider_order", return_value=["LF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider", return_value=[])
    def test_records_empty_provider_response(self, dispatch, provider_order, record, configuration):
        fetch_fixture_matches_by_date(None, "SAU", "2026-11-21")
        record.assert_called_once()
        self.assertEqual(record.call_args.args[2], "empty_response")

    @patch("predict.fixture_sources.LF_KEY", "")
    @patch("predict.fixture_sources.LIVEFOOTBALL_LEAGUE_IDS", {"SAU": 536})
    def test_lf_configuration_requires_key_without_exposing_value(self):
        from predict.fixture_sources import provider_configuration
        configured, label = provider_configuration("LF", "SAU")
        self.assertFalse(configured)
        self.assertEqual(label, "LIVEFOOTBALL_KEY")



class BulkFixtureDiscoveryTests(TestCase):
    @patch("predict.fixture_sources.fetch_fixture_matches_by_date")
    @patch("predict.providers._om_current_matches")
    @patch("predict.fixture_sources.supports_open_matchday", return_value=True)
    def test_bulk_feed_discovers_fixture_dates_without_scanning_every_day(
        self, supports, om_matches, fetch_by_date
    ):
        om_matches.return_value = [
            {
                "utcDate": "2026-11-21T11:30:00+01:00",
                "status": "TIMED",
                "homeTeam": {"name": "Alpha"},
                "awayTeam": {"name": "Beta"},
            },
            {
                "utcDate": "2026-11-22T14:00:00+01:00",
                "status": "TIMED",
                "homeTeam": {"name": "Gamma"},
                "awayTeam": {"name": "Delta"},
            },
            {
                "utcDate": "2026-11-10T15:00:00Z",
                "status": "FINISHED",
                "homeTeam": {"name": "Old"},
                "awayTeam": {"name": "Match"},
            },
        ]
        fetch_by_date.side_effect = lambda key, code, day: [
            {
                "utcDate": f"{day}T12:00:00Z",
                "status": "TIMED",
                "homeTeam": {"name": "Home"},
                "awayTeam": {"name": "Away"},
            }
        ]

        from predict.fixture_sources import fetch_upcoming_fixture_groups
        grouped = fetch_upcoming_fixture_groups(None, "TUR", days_ahead=45)

        self.assertEqual(list(grouped), ["2026-11-21", "2026-11-22"])
        self.assertEqual(fetch_by_date.call_count, 2)
        self.assertEqual(om_matches.call_count, 1)

    @patch("predict.fixture_sources.fetch_fixture_matches_by_date")
    @patch("predict.fixture_sources.supports_open_matchday", return_value=False)
    def test_non_bulk_leagues_keep_date_lookup_fallback(self, supports, fetch_by_date):
        fetch_by_date.side_effect = lambda key, code, day: (
            [{"utcDate": f"{day}T12:00:00Z"}] if day.endswith("-21") else []
        )
        with patch("predict.utils.find_upcoming_match_dates", return_value=["2026-11-21"]):
            from predict.fixture_sources import fetch_upcoming_fixture_groups
            grouped = fetch_upcoming_fixture_groups(None, "SAU", days_ahead=45)
        self.assertIn("2026-11-21", grouped)
