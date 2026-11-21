from datetime import date
from unittest.mock import patch

from django.test import TestCase

from .models import MatchPrediction
from .utils import save_predictions


class PredictionSnapshotTests(TestCase):
    def setUp(self):
        self.fixture = {
            "id": "snapshot-fixture-001",
            "homeTeam": {"name": "Home FC"},
            "awayTeam": {"name": "Away FC"},
            "utcDate": "2026-12-01T15:00:00Z",
            "status": "TIMED",
        }

    def create_prediction(self, **overrides):
        values = {
            "match_id": "snapshot-fixture-001",
            "home_team": "Home FC",
            "away_team": "Away FC",
            "match_date": date(2026, 12, 1),
            "competition": "PL",
            "predicted_home_goals": 2,
            "predicted_away_goals": 0,
            "predicted_home_rate": 1.85,
            "predicted_away_rate": 0.72,
            "predicted_result": "Home",
            "odds_home": 1.8,
            "odds_draw": 3.5,
            "odds_away": 4.2,
            "status": "TIMED",
        }
        values.update(overrides)
        return MatchPrediction.objects.create(**values)

    @patch("predict.utils.predict_match_outcome", return_value=("Home", 3, 1, 2.65, 1.15, None))
    def test_existing_upcoming_fixture_is_refreshed_in_place(self, inference):
        original = self.create_prediction()

        saved = save_predictions(
            [self.fixture],
            model_home=object(),
            model_away=object(),
            le={},
            match_date="2026-12-01",
            competition_code="PL",
        )

        inference.assert_called_once()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].pk, original.pk)
        self.assertEqual(MatchPrediction.objects.count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.predicted_home_goals, 3)
        self.assertEqual(original.predicted_away_goals, 1)
        self.assertEqual(original.predicted_home_rate, 2.65)
        self.assertEqual(original.predicted_away_rate, 1.15)
        self.assertEqual(original.predicted_result, "Home")
        # Refreshing a forecast must not erase odds when no new odds were fetched.
        self.assertEqual(original.odds_home, 1.8)
        self.assertEqual(original.odds_draw, 3.5)
        self.assertEqual(original.odds_away, 4.2)

    @patch("predict.utils.predict_match_outcome", return_value=("Away", 0, 2, 0.65, 1.9, None))
    def test_finished_fixture_keeps_its_issued_prediction_and_result(self, inference):
        original = self.create_prediction(
            status="FINISHED",
            predicted_home_goals=2,
            predicted_away_goals=1,
            actual_home_goals=2,
            actual_away_goals=1,
            is_accurate=True,
        )

        saved = save_predictions(
            [{**self.fixture, "status": "FINISHED"}],
            model_home=object(),
            model_away=object(),
            le={},
            match_date="2026-12-01",
            competition_code="PL",
        )

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].pk, original.pk)
        original.refresh_from_db()
        self.assertEqual(original.predicted_home_goals, 2)
        self.assertEqual(original.predicted_away_goals, 1)
        self.assertEqual(original.actual_home_goals, 2)
        self.assertEqual(original.actual_away_goals, 1)
        self.assertTrue(original.is_accurate)
        inference.assert_not_called()
