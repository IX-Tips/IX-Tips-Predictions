from unittest import TestCase
from unittest.mock import patch

from predict.fkf import (
    _parse_kickoff,
    fetch_matches_by_date,
    normalize_fixture,
    season_for_date,
)


class FKFAdapterTests(TestCase):
    def setUp(self):
        self.teams = {
            "home-id": {
                "team_name": "Nairobi United",
                "short_name": "NAI",
                "logo_url": "https://example.test/nairobi.png",
            },
            "away-id": {
                "team_name": "Murang'a Seal",
                "short_name": "MUR",
                "logo_url": None,
            },
        }

    def test_split_season_uses_july_as_start(self):
        self.assertEqual(season_for_date("2026-11-23"), "2026/2027")
        self.assertEqual(season_for_date("2027-02-01"), "2026/2027")
        self.assertEqual(season_for_date("2026-06-30"), "2025/2026")

    def test_kickoff_keeps_kenya_timezone(self):
        self.assertEqual(
            _parse_kickoff("2026-12-21", "15:00:00"),
            "2026-12-21T15:00:00+03:00",
        )
        self.assertEqual(_parse_kickoff("2026-12-21", None), "2026-12-21")

    def test_normalize_fixture_maps_fkf_schema(self):
        match = normalize_fixture(
            {
                "id": "fixture-123",
                "home_team_id": "home-id",
                "away_team_id": "away-id",
                "match_date": "2026-12-21",
                "match_time": "15:00:00",
                "home_score": None,
                "away_score": None,
                "status": "scheduled",
            },
            self.teams,
        )
        self.assertEqual(match["id"], "FKF-fixture-123")
        self.assertEqual(match["status"], "TIMED")
        self.assertEqual(match["homeTeam"]["name"], "Nairobi United")
        self.assertEqual(match["awayTeam"]["name"], "Murang'a Seal")
        self.assertEqual(match["utcDate"], "2026-12-21T15:00:00+03:00")

    def test_normalize_completed_fixture_keeps_scores(self):
        match = normalize_fixture(
            {
                "id": "fixture-456",
                "home_team_id": "home-id",
                "away_team_id": "away-id",
                "match_date": "2026-11-23",
                "match_time": "13:00:00",
                "home_score": 2,
                "away_score": 1,
                "status": "completed",
            },
            self.teams,
        )
        self.assertEqual(match["status"], "FINISHED")
        self.assertEqual(match["score"]["fullTime"], {"home": 2, "away": 1})

    def test_normalize_postponed_fixture_does_not_keep_scores(self):
        match = normalize_fixture(
            {
                "id": "fixture-789",
                "home_team_id": "home-id",
                "away_team_id": "away-id",
                "match_date": "2026-11-24",
                "match_time": "15:00:00",
                "home_score": None,
                "away_score": None,
                "status": "postponed",
            },
            self.teams,
        )
        self.assertEqual(match["status"], "POSTPONED")
        self.assertEqual(match["score"]["fullTime"], {"home": None, "away": None})

    def test_fixture_with_unknown_team_is_skipped(self):
        match = normalize_fixture(
            {
                "id": "fixture-999",
                "home_team_id": "missing",
                "away_team_id": "away-id",
                "match_date": "2026-11-24",
                "status": "scheduled",
            },
            self.teams,
        )
        self.assertIsNone(match)

    @patch("predict.fkf._season_matches")
    def test_fetch_by_date_filters_inside_fkf_season(self, season_matches):
        season_matches.return_value = [
            {"utcDate": "2026-11-23T13:00:00+03:00"},
            {"utcDate": "2026-11-24T15:00:00+03:00"},
        ]
        rows = fetch_matches_by_date("KEN", "2026-11-24")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["utcDate"][:10], "2026-11-24")
        season_matches.assert_called_once_with("2026/2027")
