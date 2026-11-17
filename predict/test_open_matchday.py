from unittest import TestCase
from unittest.mock import patch

from predict.providers import (
    _om_row_to_match,
    om_fetch_matches_by_date,
    supports_open_matchday,
)


class OpenMatchdayProviderTests(TestCase):
    def test_finished_csv_row_is_normalized_to_internal_match_shape(self):
        match = _om_row_to_match(
            {
                "date": "2026-11-21",
                "time": "17:30",
                "home": "Club A",
                "away": "Club B",
                "ft_home": "2",
                "ft_away": "1",
                "ht_home": "1",
                "ht_away": "0",
            },
            "BEL",
            "2627",
            "results",
        )

        self.assertEqual(match["status"], "FINISHED")
        self.assertEqual(match["homeTeam"]["name"], "Club A")
        self.assertEqual(match["score"]["fullTime"], {"home": 2, "away": 1})
        self.assertEqual(match["score"]["halfTime"], {"home": 1, "away": 0})
        self.assertEqual(match["utcDate"], "2026-11-21T17:30:00+01:00")

    def test_fixture_row_does_not_treat_blank_scores_as_zero(self):
        match = _om_row_to_match(
            {
                "date": "2026-11-22",
                "time": "",
                "home": "Club A",
                "away": "Club B",
                "ft_home": "",
                "ft_away": "",
            },
            "BEL",
            "2627",
            "fixtures",
        )

        self.assertEqual(match["status"], "TIMED")
        self.assertIsNone(match["score"]["fullTime"]["home"])
        self.assertIsNone(match["score"]["fullTime"]["away"])

    def test_only_supported_competitions_use_open_matchday(self):
        with patch("predict.providers.OM_ENABLED", True):
            self.assertTrue(supports_open_matchday("BEL"))
            self.assertTrue(supports_open_matchday("JPN"))
            self.assertFalse(supports_open_matchday("SAU"))

    def test_date_fetch_filters_the_current_feed_by_match_day(self):
        matches = [
            {"utcDate": "2026-11-21T15:00:00+01:00", "homeTeam": {"name": "A"}},
            {"utcDate": "2026-11-22T15:00:00+01:00", "homeTeam": {"name": "B"}},
        ]
        with patch("predict.providers._om_current_matches", return_value=matches):
            result = om_fetch_matches_by_date("BEL", "2026-11-21")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["homeTeam"]["name"], "A")
