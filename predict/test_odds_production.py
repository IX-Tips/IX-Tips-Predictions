from datetime import date
from unittest.mock import patch

from django.test import TestCase

from .models import MatchOdds, MatchPrediction
from .views import (
    extract_odds_from_bookmaker,
    update_odds_in_db,
    upsert_match_odds,
)


class ProductionOddsUpdaterTests(TestCase):
    def make_prediction(self, competition="KEN", home="Gor Mahia", away="AFC Leopards"):
        return MatchPrediction.objects.create(
            competition=competition,
            match_date=date(2026, 11, 28),
            home_team=home,
            away_team=away,
        )

    def test_extracts_all_api_football_market_shapes(self):
        game = {"home_team": "Gor Mahia", "away_team": "AFC Leopards"}
        bookmaker = {
            "title": "Example",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Gor Mahia", "price": 2.0},
                    {"name": "Draw", "price": 3.0},
                    {"name": "AFC Leopards", "price": 4.0},
                ]},
                {"key": "over_under", "outcomes": [
                    {"name": "Over 2.5", "price": 1.9},
                    {"name": "Under 2.5", "price": 1.8},
                ]},
                {"key": "btts", "outcomes": [
                    {"name": "Yes", "price": 1.7},
                    {"name": "No", "price": 2.1},
                ]},
            ],
        }

        odds = extract_odds_from_bookmaker(bookmaker, game)

        self.assertEqual(odds, {
            "home": 2.0,
            "draw": 3.0,
            "away": 4.0,
            "over25": 1.9,
            "under25": 1.8,
            "btts_yes": 1.7,
            "btts_no": 2.1,
        })

    def test_partial_upsert_preserves_existing_prices_and_sources(self):
        prediction = self.make_prediction()
        MatchOdds.objects.create(
            match=prediction,
            home_win=1.8,
            draw=3.2,
            away_win=4.1,
            over_2_5=1.9,
            bookmaker="Existing",
            market_sources={"home": "Existing", "over25": "Existing"},
        )

        upsert_match_odds(prediction, {
            "home": 2.0,
            "draw": None,
            "away": None,
            "over25": None,
            "under25": 1.95,
            "btts_yes": None,
            "btts_no": None,
            "bookmaker": "Fallback",
            "market_sources": {"home": "Fallback", "under25": "Fallback"},
        })

        saved = MatchOdds.objects.get(match=prediction)
        self.assertEqual(saved.home_win, 2.0)
        self.assertEqual(saved.draw, 3.2)
        self.assertEqual(saved.away_win, 4.1)
        self.assertEqual(saved.over_2_5, 1.9)
        self.assertEqual(saved.under_2_5, 1.95)
        self.assertEqual(saved.bookmaker, "Fallback")
        self.assertEqual(saved.market_sources["over25"], "Existing")
        self.assertEqual(saved.market_sources["under25"], "Fallback")

    @patch("predict.views.fetch_odds_for_date")
    @patch("predict.views.fetch_odds")
    def test_kenya_uses_date_scoped_api_football_and_persists_odds(
        self, fetch_primary, fetch_fallback
    ):
        prediction = self.make_prediction()
        fetch_fallback.return_value = [{
            "home_team": "Gor Mahia FC",
            "away_team": "AFC Leopards",
            "bookmakers": [{
                "title": "API-Football Bookmaker",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Gor Mahia FC", "price": 1.75},
                        {"name": "Draw", "price": 3.4},
                        {"name": "AFC Leopards", "price": 4.8},
                    ]},
                    {"key": "over_under", "outcomes": [
                        {"name": "Over 2.5", "price": 2.0},
                        {"name": "Under 2.5", "price": 1.7},
                    ]},
                    {"key": "btts", "outcomes": [
                        {"name": "Yes", "price": 1.8},
                        {"name": "No", "price": 2.0},
                    ]},
                ],
            }],
        }]

        updated = update_odds_in_db("KEN", match_dates=[prediction.match_date])

        self.assertEqual(updated, 1)
        fetch_primary.assert_not_called()
        fetch_fallback.assert_called_once_with(
            odds_api_key="", sport_key=None, match_date="2026-11-28"
        )
        saved = MatchOdds.objects.get(match=prediction)
        self.assertEqual(saved.home_win, 1.75)
        self.assertEqual(saved.draw, 3.4)
        self.assertEqual(saved.away_win, 4.8)
        self.assertEqual(saved.over_2_5, 2.0)
        self.assertEqual(saved.under_2_5, 1.7)
        self.assertEqual(saved.btts_yes, 1.8)
        self.assertEqual(saved.btts_no, 2.0)

    @patch("predict.views.fetch_odds_for_date")
    @patch("predict.views.fetch_odds")
    def test_reversed_provider_teams_swap_home_and_away_prices(
        self, fetch_primary, fetch_fallback
    ):
        prediction = self.make_prediction(home="Home Club", away="Away Club")
        fetch_fallback.return_value = [{
            "home_team": "Away Club",
            "away_team": "Home Club",
            "bookmakers": [{
                "title": "Fallback",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Away Club", "price": 2.2},
                    {"name": "Draw", "price": 3.1},
                    {"name": "Home Club", "price": 3.5},
                ]}],
            }],
        }]

        self.assertEqual(
            update_odds_in_db("KEN", match_dates=[prediction.match_date]), 1
        )
        saved = MatchOdds.objects.get(match=prediction)
        self.assertEqual(saved.home_win, 3.5)
        self.assertEqual(saved.draw, 3.1)
        self.assertEqual(saved.away_win, 2.2)

    @patch("predict.views.fetch_odds_for_date")
    @patch("predict.views.fetch_odds")
    def test_nigeria_is_routed_to_fallback_without_primary_sport_key(
        self, fetch_primary, fetch_fallback
    ):
        prediction = self.make_prediction(
            competition="NGA", home="Enyimba", away="Kano Pillars"
        )
        fetch_fallback.return_value = []

        self.assertEqual(
            update_odds_in_db("NGA", match_dates=[prediction.match_date]), 0
        )
        fetch_primary.assert_not_called()
        fetch_fallback.assert_called_once()
