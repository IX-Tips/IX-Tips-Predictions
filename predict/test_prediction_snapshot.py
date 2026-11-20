from datetime import date
from unittest.mock import patch

from django.test import TestCase

from .models import MatchPrediction
from .utils import save_predictions


class PredictionSnapshotTests(TestCase):
    def test_existing_fixture_is_not_repredicted_or_overwritten(self):
        original = MatchPrediction.objects.create(
            match_id="snapshot-fixture-001",
            home_team="Home FC",
            away_team="Away FC",
            match_date=date(2026, 12, 1),
            competition="PL",
            predicted_home_goals=2,
            predicted_away_goals=0,
            predicted_home_rate=1.85,
            predicted_away_rate=0.72,
            predicted_result="Home",
            status="TIMED",
        )
        fixture = {
            "id": "snapshot-fixture-001",
            "homeTeam": {"name": "Home FC"},
            "awayTeam": {"name": "Away FC"},
            "utcDate": "2026-12-01T15:00:00Z",
            "status": "TIMED",
        }

        with patch("predict.utils.predict_match_outcome") as inference:
            saved = save_predictions(
                [fixture],
                model_home=object(),
                model_away=object(),
                le={},
                match_date="2026-12-01",
                competition_code="PL",
            )

        inference.assert_not_called()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].pk, original.pk)
        original.refresh_from_db()
        self.assertEqual(original.predicted_home_goals, 2)
        self.assertEqual(original.predicted_away_goals, 0)
        self.assertEqual(original.predicted_home_rate, 1.85)
        self.assertEqual(original.predicted_away_rate, 0.72)
        self.assertEqual(original.status, "TIMED")
