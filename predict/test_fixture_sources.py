from unittest import TestCase
from unittest.mock import Mock, patch

from django.utils import timezone

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
    def test_primary_fixture_response_skips_secondary_provider(self, dispatch, provider_order, configuration):
        second = {
            **self.fixture,
            "id": 12345,
            "homeTeam": {"name": "Alpha", "crest": "crest.png"},
        }
        dispatch.side_effect = [[self.fixture], [second]]

        matches = fetch_fixture_matches_by_date(None, "BEL", "2026-11-10")

        self.assertEqual(len(matches), 1)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(matches[0]["id"], "uk-1")

    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._provider_order", return_value=["UK", "AF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    def test_empty_primary_response_uses_secondary_fallback(self, dispatch, provider_order, configuration):
        dispatch.side_effect = [[], [self.fixture]]

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
    @patch("predict.providers.AF_KEY", "configured-test-key")
    @patch("predict.providers.cache.set")
    @patch("predict.providers.requests.get")
    def test_af_get_records_api_errors_in_provider_health(self, get, cache_set):
        response = Mock()
        response.json.return_value = {
            "errors": {"token": "Invalid API key"},
            "response": [],
        }
        get.return_value = response

        from predict.providers import _af_get
        self.assertEqual(_af_get("fixtures", {"league": 123, "season": 2026}), [])

        health_writes = [
            call for call in cache_set.call_args_list
            if call.args[0] == "provider_request_health::AF::fixtures"
        ]
        self.assertEqual(len(health_writes), 1)
        self.assertEqual(health_writes[0].args[1]["status"], "error")
        self.assertIn("Invalid API key", health_writes[0].args[1]["last_error"])

    @patch("predict.providers.AF_KEY", "configured-test-key")
    @patch("predict.providers.cache.set")
    @patch("predict.providers.requests.get")
    def test_af_get_records_successful_empty_response_as_healthy(self, get, cache_set):
        response = Mock()
        response.json.return_value = {"errors": [], "response": [], "paging": {"total": 1, "current": 1}}
        get.return_value = response

        from predict.providers import _af_get
        self.assertEqual(_af_get("fixtures", {"league": 123, "season": 2026}), [])

        health = next(
            call.args[1] for call in cache_set.call_args_list
            if call.args[0] == "provider_request_health::AF::fixtures"
        )
        self.assertEqual(health["status"], "ok")
        self.assertIsNone(health["last_error"])

    @patch("predict.fixture_sources._diagnostic_cache_get", return_value={
        "status": "error",
        "last_error": "Invalid API key",
        "checked_at": timezone.now().isoformat(),
        "endpoint": "fixtures",
    })
    @patch("predict.fixture_sources.record_provider_diagnostic")
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._provider_order", return_value=["AF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider", return_value=[])
    def test_recent_api_football_request_error_is_not_reported_as_empty_response(
        self, dispatch, provider_order, configuration, record, health
    ):
        fetch_fixture_matches_by_date(None, "KEN", "2026-11-21")

        self.assertEqual(record.call_args.args[2], "error")
        self.assertEqual(record.call_args.kwargs["error"], "Invalid API key")

    @patch("predict.providers.APIFOOTBALL_API_KEY", "django-setting-key")
    @patch.dict("os.environ", {"APIFOOTBALL_KEY": ""}, clear=False)
    def test_api_football_key_accepts_django_setting_name(self):
        from predict.providers import _configured_api_football_key

        self.assertEqual(_configured_api_football_key(), "django-setting-key")

    @patch("predict.providers.APIFOOTBALL_API_KEY", "django-setting-key")
    @patch.dict("os.environ", {"APIFOOTBALL_KEY": "legacy-env-key"}, clear=False)
    def test_legacy_api_football_key_env_alias_takes_precedence(self):
        from predict.providers import _configured_api_football_key

        self.assertEqual(_configured_api_football_key(), "legacy-env-key")

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
    @patch("predict.fixture_sources._diagnostic_cache_get", return_value={})
    @patch("predict.fixture_sources._dispatch_explicit_provider", return_value=[])
    def test_records_empty_provider_response(self, dispatch, health, provider_order, record, configuration):
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

    @patch("predict.fixture_sources.AF_KEY", "configured-test-key")
    @patch("predict.fixture_sources.APIFOOTBALL_LEAGUE_SEARCH", {"KEN": ("Kenya", "FKF Premier League")})
    def test_africa_uses_api_football_only_as_a_configured_fallback(self):
        from predict.fixture_sources import _provider_order

        self.assertEqual(_provider_order("KEN"), ["OF", "AF"])

    @patch("predict.providers.cache.set")
    @patch("predict.providers.cache.get", return_value=None)
    @patch("predict.providers._af_get")
    @patch("predict.providers.APIFOOTBALL_LEAGUE_SEARCH", {"KEN": ("Kenya", "FKF Premier League")})
    def test_af_league_id_uses_exact_v3_country_and_name_lookup(
        self, lookup, cache_get, cache_set
    ):
        lookup.return_value = [
            {"league": {"id": 999, "name": "Premier League"}, "country": {"name": "Kenya"}},
            {"league": {"id": 123, "name": "FKF Premier League"}, "country": {"name": "Kenya"}},
            {"league": {"id": 456, "name": "FKF Premier League"}, "country": {"name": "Uganda"}},
        ]

        from predict.providers import af_league_id
        self.assertEqual(af_league_id("KEN", 2026), 123)
        lookup.assert_called_once_with(
            "leagues", {"search": "FKF Premier League", "country": "Kenya", "season": 2026}
        )

    @patch("predict.providers.cache.set")
    @patch("predict.providers.cache.get", return_value=None)
    @patch("predict.providers._af_get", return_value=[
        {"league": {"id": 123, "name": "Premier League"}, "country": {"name": "Uganda"}}
    ])
    @patch("predict.providers.APIFOOTBALL_LEAGUE_SEARCH", {"KEN": ("Kenya", "FKF Premier League")})
    def test_af_league_id_rejects_wrong_country_or_name(
        self, lookup, cache_get, cache_set
    ):
        from predict.providers import af_league_id
        self.assertIsNone(af_league_id("KEN", 2026))

    def test_calendar_year_african_leagues_use_the_current_year(self):
        from datetime import date
        from predict.providers import af_season

        self.assertEqual(af_season("MWI", date(2026, 2, 1)), 2026)
        self.assertEqual(af_season("ZWE", date(2026, 2, 1)), 2026)

    @patch("predict.openfootball._latest_available_season_year", return_value=2025)
    @patch("predict.openfootball.current_season_year", return_value=2026)
    @patch("predict.fixture_sources.record_provider_diagnostic")
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._provider_order", return_value=["OF"])
    @patch("predict.fixture_sources._dispatch_explicit_provider", return_value=[])
    def test_openfootball_diagnostic_explains_missing_current_season(
        self, dispatch, provider_order, configuration, record, current_year, latest_year
    ):
        fetch_fixture_matches_by_date(None, "KEN", "2026-11-21")
        self.assertEqual(record.call_args.args[2], "empty_response")
        self.assertIn("latest published season starts 2025", record.call_args.kwargs["error"])



class BulkFixtureDiscoveryTests(TestCase):
    @patch("predict.fixture_sources.fetch_fixture_matches_by_date")
    @patch("predict.providers._om_current_matches")
    @patch("predict.fixture_sources.supports_open_matchday", return_value=True)
    def test_bulk_feed_never_includes_the_eighth_calendar_day(
        self, supports, om_matches, fetch_by_date
    ):
        from datetime import date, timedelta
        today = date.today()
        sixth_day = today + timedelta(days=6)
        eighth_day = today + timedelta(days=7)
        om_matches.return_value = [
            {
                "utcDate": f"{sixth_day.isoformat()}T11:30:00Z",
                "status": "TIMED",
                "homeTeam": {"name": "Within Window"},
                "awayTeam": {"name": "Visitor"},
            },
            {
                "utcDate": f"{eighth_day.isoformat()}T14:00:00Z",
                "status": "TIMED",
                "homeTeam": {"name": "Outside Window"},
                "awayTeam": {"name": "Visitor"},
            },
        ]
        fetch_by_date.side_effect = lambda key, code, day: [{
            "utcDate": f"{day}T12:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Home"},
            "awayTeam": {"name": "Away"},
        }]

        from predict.fixture_sources import fetch_upcoming_fixture_groups
        grouped = fetch_upcoming_fixture_groups(None, "EFLCUP", days_ahead=45)

        self.assertEqual(list(grouped), [sixth_day.isoformat()])
        fetch_by_date.assert_called_once_with(None, "EFLCUP", sixth_day.isoformat())

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
        grouped = fetch_upcoming_fixture_groups(None, "EFLCUP", days_ahead=45)

        self.assertEqual(list(grouped), ["2026-11-21", "2026-11-22"])
        self.assertEqual(fetch_by_date.call_count, 2)
        self.assertEqual(om_matches.call_count, 1)

    @patch("predict.utils.find_upcoming_match_dates", return_value=["2026-11-21"])
    @patch("predict.fixture_sources.fetch_fixture_matches_by_date")
    @patch("predict.providers.of_current_matches", return_value=[])
    @patch("predict.fixture_sources.supports_openfootball", return_value=True)
    @patch("predict.fixture_sources.supports_open_matchday", return_value=False)
    @patch("predict.fixture_sources.get_provider", return_value="OF")
    def test_empty_openfootball_season_uses_configured_date_fallback(
        self, provider, supports_om, supports_of, of_matches, fetch_by_date, find_dates
    ):
        fetch_by_date.return_value = [{
            "utcDate": "2026-11-21T15:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "African Club"},
            "awayTeam": {"name": "Visiting Club"},
        }]

        from predict.fixture_sources import fetch_upcoming_fixture_groups
        grouped = fetch_upcoming_fixture_groups(None, "KEN", days_ahead=45)

        self.assertIn("2026-11-21", grouped)
        find_dates.assert_called_once()
        fetch_by_date.assert_called_once_with(None, "KEN", "2026-11-21")

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



class ResultProviderFallbackTests(TestCase):
    def setUp(self):
        self.fixture = {
            "id": "primary-1",
            "utcDate": "2026-11-10T15:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Alpha FC"},
            "awayTeam": {"name": "Beta"},
            "score": {"fullTime": {"home": None, "away": None}},
        }

    @patch("predict.fixture_sources._result_provider_order", return_value=["FD", "OM"])
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    @patch("predict.fixture_sources.record_result_provider_diagnostic")
    def test_fallback_result_fills_primary_fixture_without_result(
        self, record, dispatch, configuration, provider_order
    ):
        completed = {
            **self.fixture,
            "id": "om-1",
            "status": "FINISHED",
            "score": {"fullTime": {"home": 2, "away": 1}},
        }
        dispatch.side_effect = [[self.fixture], [completed]]
        from predict.fixture_sources import fetch_result_matches_by_date
        matches = fetch_result_matches_by_date(
            "BEL", "2026-11-10", target_pairs={("alpha", "beta")},
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 2, "away": 1})
        self.assertEqual(matches[0]["status"], "FINISHED")
        self.assertEqual(dispatch.call_count, 2)

    @patch("predict.fixture_sources._result_provider_order", return_value=["FD", "OM"])
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    @patch("predict.fixture_sources.record_result_provider_diagnostic")
    def test_primary_complete_result_keeps_precedence_over_fallback(
        self, record, dispatch, configuration, provider_order
    ):
        primary_result = {
            **self.fixture, "status": "FINISHED",
            "score": {"fullTime": {"home": 1, "away": 0}},
        }
        fallback_result = {
            **self.fixture, "id": "om-1", "status": "FINISHED",
            "score": {"fullTime": {"home": 2, "away": 0}},
        }
        dispatch.side_effect = [[primary_result], [fallback_result]]
        from predict.fixture_sources import fetch_result_matches_by_date
        matches = fetch_result_matches_by_date("BEL", "2026-11-10")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 1, "away": 0})


    @patch("predict.fixture_sources._result_provider_order", return_value=["FD", "OM"])
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    @patch("predict.fixture_sources.record_result_provider_diagnostic")
    def test_skips_fallback_when_primary_resolves_every_target(
        self, record, dispatch, configuration, provider_order
    ):
        completed = {
            **self.fixture, "status": "FINISHED",
            "score": {"fullTime": {"home": 1, "away": 0}},
        }
        dispatch.return_value = [completed]
        from predict.fixture_sources import fetch_result_matches_by_date
        matches = fetch_result_matches_by_date(
            "BEL", "2026-11-10", target_pairs={("alpha", "beta")},
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 1, "away": 0})



class CrossProviderFixtureIdentityTests(TestCase):
    def test_team_names_with_common_provider_variants_share_identity(self):
        from predict.fixture_sources import _team_key

        pairs = [
            ("Manchester United", "Man Utd"),
            ("Paris Saint-Germain", "PSG"),
            ("Bayern München", "Bayern Munich"),
            ("Internazionale", "Inter Milan"),
            ("Sporting Clube de Portugal", "Sporting CP"),
            ("Athletic Club", "Athletic Bilbao"),
        ]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(_team_key(left), _team_key(right))

    def test_fixture_key_normalizes_equivalent_timezone_offsets(self):
        from predict.fixture_sources import _fixture_key

        utc_fixture = {
            "utcDate": "2026-11-10T23:30:00Z",
            "homeTeam": {"name": "Manchester United"},
            "awayTeam": {"name": "PSG"},
        }
        offset_fixture = {
            "utcDate": "2026-11-11T01:30:00+02:00",
            "homeTeam": {"name": "Man Utd"},
            "awayTeam": {"name": "Paris Saint-Germain"},
        }
        self.assertEqual(_fixture_key(utc_fixture), _fixture_key(offset_fixture))

    def test_fixture_key_rejects_invalid_kickoff(self):
        from predict.fixture_sources import _fixture_key

        fixture = {
            "utcDate": "not-a-date",
            "homeTeam": {"name": "Alpha"},
            "awayTeam": {"name": "Beta"},
        }
        self.assertIsNone(_fixture_key(fixture))


    @patch("predict.fixture_sources._result_provider_order", return_value=["FD", "OM"])
    @patch("predict.fixture_sources.provider_configuration", return_value=(True, ""))
    @patch("predict.fixture_sources._dispatch_explicit_provider")
    @patch("predict.fixture_sources.record_result_provider_diagnostic")
    def test_fallback_matches_same_fixture_when_provider_date_differs_by_one_day(
        self, record, dispatch, configuration, provider_order
    ):
        primary = {
            "utcDate": "2026-11-10T23:30:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Alpha FC"},
            "awayTeam": {"name": "Beta"},
            "score": {"fullTime": {"home": None, "away": None}},
        }
        fallback = {
            "id": "om-1",
            "utcDate": "2026-11-11T00:15:00Z",
            "status": "FINISHED",
            "homeTeam": {"name": "Alpha"},
            "awayTeam": {"name": "Beta"},
            "score": {"fullTime": {"home": 3, "away": 1}},
        }
        dispatch.side_effect = [[primary], [fallback]]

        from predict.fixture_sources import fetch_result_matches_by_date
        matches = fetch_result_matches_by_date("BEL", "2026-11-10")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 3, "away": 1})


class AfricanLeagueLookupValidationTests(TestCase):
    @patch("predict.providers.cache.set")
    @patch("predict.providers.cache.get", return_value=None)
    @patch("predict.providers._af_get", return_value=[
        {"league": {"id": "bad-id", "name": "FKF Premier League"}, "country": {"name": "Kenya"}},
        {"league": {"id": -5, "name": "FKF Premier League"}, "country": {"name": "Kenya"}},
        {"league": {"id": 321, "name": "FKF Premier League"}, "country": {"name": "Kenya"}},
    ])
    @patch("predict.providers.APIFOOTBALL_LEAGUE_SEARCH", {"KEN": ("Kenya", "FKF Premier League")})
    def test_ignores_invalid_ids_and_uses_valid_exact_match(self, lookup, cache_get, cache_set):
        from predict.providers import af_league_id
        self.assertEqual(af_league_id("KEN", 2026), 321)

    @patch("predict.providers.cache.set")
    @patch("predict.providers.cache.get", return_value=None)
    @patch("predict.providers._af_get", return_value=[
        {"league": "unexpected-shape"},
        {"league": {"id": 321, "name": "FKF Premier League"}, "country": None},
        {"league": {"id": 0, "name": "FKF Premier League"}, "country": {"name": "Kenya"}},
    ])
    @patch("predict.providers.APIFOOTBALL_LEAGUE_SEARCH", {"KEN": ("Kenya", "FKF Premier League")})
    def test_malformed_lookup_rows_do_not_raise_or_accept_invalid_ids(self, lookup, cache_get, cache_set):
        from predict.providers import af_league_id
        self.assertIsNone(af_league_id("KEN", 2026))
