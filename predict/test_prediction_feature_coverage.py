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

    def test_provider_name_variants_share_rolling_form_elo_and_h2h_history(self):
        history = pd.DataFrame([
            {
                "home_team": "Man Utd",
                "away_team": "Everton FC",
                "home_goals": 2,
                "away_goals": 0,
                "utc_date": "2025-01-01T15:00:00Z",
            },
            {
                "home_team": "Manchester United FC",
                "away_team": "Liverpool FC",
                "home_goals": 1,
                "away_goals": 1,
                "utc_date": "2025-01-08T15:00:00Z",
            },
            {
                "home_team": "Manchester United",
                "away_team": "Liverpool",
                "home_goals": 3,
                "away_goals": 1,
                "utc_date": "2025-01-15T15:00:00Z",
            },
        ])

        features, _, _, context = build_training_features(history)

        # The third fixture must see both earlier United matches even though
        # the provider changed the club's display name between rows.
        self.assertEqual(features.iloc[2]["home_matches_seen"], 2)
        self.assertEqual(features.iloc[2]["h2h_match_count"], 1)
        self.assertEqual(len([
            key for key in context["team_profiles"]
            if key == "manchester united"
        ]), 1)
        self.assertIn(("liverpool", "manchester united"), context["h2h_profiles"])

    def test_provider_aliases_resolve_to_the_same_team_identity(self):
        from .utils import _normalize_team_lookup_value

        equivalent_names = [
            ("Man Utd", "Manchester United FC"),
            ("Athletic Club", "Athletic Bilbao"),
            ("Charleroi", "Royal Charleroi S.C."),
            ("Sporting Lisbon", "Sporting CP"),
            ("Olympique Marseille", "Marseille"),
        ]
        for provider_name, canonical_name in equivalent_names:
            with self.subTest(provider_name=provider_name):
                self.assertEqual(
                    _normalize_team_lookup_value(provider_name),
                    _normalize_team_lookup_value(canonical_name),
                )

    def test_goal_rate_bias_is_shrunk_and_capped_from_holdout_residuals(self):
        actual = [1.5] * 20
        predicted = [1.0] * 20
        self.assertAlmostEqual(_estimate_goal_rate_bias(actual, predicted), 0.325)

        self.assertEqual(_estimate_goal_rate_bias([2.0] * 10, [1.0] * 10), 0.0)
        self.assertEqual(_estimate_goal_rate_bias([3.0] * 20, [1.0] * 20), 0.35)
