from django.test import SimpleTestCase
import pandas as pd

from .utils import _estimate_goal_rate_bias, build_training_features


class PredictionFeatureCoverageTests(SimpleTestCase):
    def test_rolling_features_expose_prior_match_coverage_without_future_leakage(self):
        history = pd.DataFrame([
            {
                "home_team": "Alpha FC",
                "away_team": "Beta FC",
                "home_goals": 2,
                "away_goals": 0,
                "utc_date": "2025-01-01T15:00:00Z",
            },
            {
                "home_team": "Alpha FC",
                "away_team": "Gamma FC",
                "home_goals": 1,
                "away_goals": 1,
                "utc_date": "2025-01-08T15:00:00Z",
            },
        ])

        features, _, _, _ = build_training_features(history)

        # A fixture's features must only include matches played before it.
        self.assertEqual(features.iloc[0]["home_matches_seen"], 0)
        self.assertEqual(features.iloc[0]["away_matches_seen"], 0)
        self.assertEqual(features.iloc[1]["home_matches_seen"], 1)
        self.assertEqual(features.iloc[1]["away_matches_seen"], 0)
        self.assertEqual(features.iloc[1]["h2h_match_count"], 0)

    def test_goal_rate_bias_is_shrunk_and_capped_from_holdout_residuals(self):
        actual = [1.5] * 20
        predicted = [1.0] * 20
        self.assertAlmostEqual(_estimate_goal_rate_bias(actual, predicted), 0.325)

        self.assertEqual(_estimate_goal_rate_bias([2.0] * 10, [1.0] * 10), 0.0)
        self.assertEqual(_estimate_goal_rate_bias([3.0] * 20, [1.0] * 20), 0.35)
