from datetime import datetime, timezone as dt_timezone
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from predict.utils import build_training_features, scoreline_predictions, _poisson_best_scoreline


class ChronologicalFeatureTests(TestCase):
    def test_historical_features_do_not_seed_from_full_history_global_elo(self):
        history = pd.DataFrame([{
            "home_team": "Alpha FC",
            "away_team": "Beta FC",
            "home_goals": 2,
            "away_goals": 0,
            "utc_date": datetime(2025, 1, 1, tzinfo=dt_timezone.utc),
        }])

        with patch("predict.utils._get_global_elo", side_effect=AssertionError("future Elo must not be loaded")):
            features, _, _, context = build_training_features(history)

        self.assertEqual(features.iloc[0]["home_elo"], 1500.0)
        self.assertEqual(features.iloc[0]["away_elo"], 1500.0)
        self.assertGreater(context["elo_ratings"]["Alpha FC"], 1500.0)
        self.assertLess(context["elo_ratings"]["Beta FC"], 1500.0)


class ScorelineDistributionTests(TestCase):
    def test_correct_score_probabilities_are_normalized_after_low_score_adjustment(self):
        rows = scoreline_predictions(1.35, 1.10, max_goals=6, top_n=49)

        self.assertAlmostEqual(sum(row["probability"] for row in rows), 1.0, places=8)
        self.assertTrue(all(row["probability"] >= 0 for row in rows))

    def test_best_scoreline_is_deterministic_and_valid(self):
        first = _poisson_best_scoreline(1.35, 1.10, seed="mode")
        second = _poisson_best_scoreline(1.35, 1.10, seed="mode")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertTrue(all(isinstance(goals, int) and goals >= 0 for goals in first))
