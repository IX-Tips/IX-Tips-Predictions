from unittest import TestCase
from unittest.mock import Mock, patch

from predict.external_fixture_providers import (
    _parse_fc_match, _parse_se_event, _se_tournament_id,
    football_charts_fetch_matches_by_date, sportsevents_fetch_matches_by_date,
)


class ExternalFixtureProviderTests(TestCase):
    def test_football_charts_normalizes_utc_fixture(self):
        match = _parse_fc_match({
            "slug": "algeria/fixture-1", "home_team": "Home", "away_team": "Away",
            "match_date": "2026-11-27", "time": "16:45", "status": "scheduled",
        }, "ALG")
        self.assertEqual(match["utcDate"], "2026-11-27T16:45:00Z")
        self.assertEqual(match["status"], "TIMED")
        self.assertTrue(match["id"].startswith("FC-"))
        self.assertIsNone(match["score"]["fullTime"]["home"])

    def test_football_charts_handles_postponed_status(self):
        match = _parse_fc_match({
            "home_team": "Home", "away_team": "Away",
            "match_date": "2026-11-27", "status": "postponed",
        }, "ALG")
        self.assertEqual(match["status"], "POSTPONED")

    def test_sportsevents_uses_utc_starts_at_and_parses_result(self):
        match = _parse_se_event({
            "id": 42, "teamHome": "Home", "teamAway": "Away",
            "startsAt": "2026-11-27T18:30:00+02:00", "result": "2:1",
        }, "KEN")
        self.assertEqual(match["utcDate"], "2026-11-27T16:30:00Z")
        self.assertEqual(match["status"], "FINISHED")
        self.assertEqual(match["score"]["fullTime"], {"home": 2, "away": 1})

    def test_algeria_football_charts_mapping_uses_verified_slug(self):
        with patch("predict.external_fixture_providers._fc_catalog", return_value=[
            {"league": "algir1", "name": "Ligue 1", "country": "Algeria"}
        ]):
            from predict.external_fixture_providers import _fc_league_slug
            self.assertEqual(_fc_league_slug("ALG"), "algir1")

    @patch("predict.external_fixture_providers._se_tournaments", return_value=[
        {"id": 11, "name": "FKF Premier League"}
    ])
    def test_kenya_sportsevents_mapping_is_not_enabled(self, tournaments):
        from predict.external_fixture_providers import supports_sportsevents
        self.assertFalse(supports_sportsevents("KEN"))
        tournaments.assert_not_called()

    @patch("predict.external_fixture_providers.cache.get", return_value=None)
    @patch("predict.external_fixture_providers.cache.set")
    @patch("predict.external_fixture_providers._fc_catalog", return_value=[
        {"league": "algir1", "name": "Ligue 1"}
    ])
    @patch("predict.external_fixture_providers._get_json", side_effect=RuntimeError("429 quota"))
    def test_football_charts_api_errors_are_not_cached_as_empty(
        self, get_json, catalog, cache_set, cache_get
    ):
        with patch("predict.external_fixture_providers.FC_KEY", "test-key"):
            with self.assertRaisesRegex(RuntimeError, "429"):
                football_charts_fetch_matches_by_date("ALG", "2026-11-27")
        cache_set.assert_not_called()

    @patch("predict.external_fixture_providers.cache.get", return_value=None)
    @patch("predict.external_fixture_providers.cache.set")
    @patch("predict.external_fixture_providers._se_tournament_id", return_value=7)
    @patch("predict.external_fixture_providers._get_json", side_effect=RuntimeError("429 quota"))
    def test_sportsevents_quota_error_is_not_cached_as_empty(
        self, get_json, tournament, cache_set, cache_get
    ):
        from datetime import date, timedelta
        day = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(RuntimeError, "429"):
            sportsevents_fetch_matches_by_date("ALG", day)
        cache_set.assert_not_called()


class ExternalProviderRoutingTests(TestCase):
    def test_existing_primary_then_football_charts_then_sportsevents(self):
        from predict.fixture_sources import _provider_order
        with patch("predict.fixture_sources.FIXTURE_PROVIDER_ORDER", {}), \
             patch("predict.fixture_sources.get_provider", return_value="OF"), \
             patch("predict.fixture_sources.AF_KEY", ""), \
             patch("predict.fixture_sources.supports_open_matchday", return_value=False), \
             patch("predict.external_fixture_providers.supports_football_charts", return_value=True), \
             patch("predict.external_fixture_providers.supports_sportsevents", return_value=True):
            self.assertEqual(_provider_order("ALG"), ["OF", "FC", "SE"])

    def test_existing_kenya_uganda_nigeria_sources_are_not_replaced(self):
        from predict.fixture_sources import _provider_order
        with patch("predict.fixture_sources.FIXTURE_PROVIDER_ORDER", {}), \
             patch("predict.fixture_sources.get_provider", side_effect=lambda code: {
                 "KEN": "FKF", "UGA": "OF", "NGA": "NPFL"
             }[code]), \
             patch("predict.fixture_sources.AF_KEY", ""), \
             patch("predict.fixture_sources.supports_open_matchday", return_value=False), \
             patch("predict.external_fixture_providers.supports_football_charts", return_value=False), \
             patch("predict.external_fixture_providers.supports_sportsevents", return_value=False):
            self.assertEqual(_provider_order("KEN"), ["FKF"])
            self.assertEqual(_provider_order("UGA"), ["OF"])
            self.assertEqual(_provider_order("NGA"), ["NPFL"])

    def test_fixture_handlers_are_registered(self):
        from predict.providers import _PROVIDER_DISPATCH
        self.assertTrue(callable(_PROVIDER_DISPATCH["FC"].get("fetch_matches_by_date")))
        self.assertTrue(callable(_PROVIDER_DISPATCH["SE"].get("fetch_matches_by_date")))
