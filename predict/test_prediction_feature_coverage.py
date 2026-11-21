from unittest.mock import patch

from django.test import SimpleTestCase
import pandas as pd

from .utils import (
    _estimate_goal_rate_bias,
    build_fixture_features,
    build_training_features,
    compute_cross_league_elo,
    summarize_competition_feature_coverage,
)


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

    @patch("predict.utils.fetch_training_data_all_seasons")
    def test_cross_competition_elo_aggregates_provider_name_variants(self, fetch_history):
        fetch_history.return_value = pd.DataFrame([
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
        ])

        with patch("predict.constants.COMPETITIONS", {"PL": "Premier League"}):
            ratings, _ = compute_cross_league_elo()

        self.assertIn("manchester united", ratings)
        self.assertNotIn("man utd", ratings)
        self.assertNotIn("manchester united fc", ratings)

    def test_competition_coverage_reports_partial_history_without_inference(self):
        history = pd.DataFrame([
            {"competition": "PL"},
            {"competition": "PL"},
            {"competition": "PD"},
        ])
        features = pd.DataFrame([
            {"home_matches_seen": 0, "away_matches_seen": 0, "h2h_match_count": 0},
            {"home_matches_seen": 4, "away_matches_seen": 2, "h2h_match_count": 1},
            {"home_matches_seen": 1, "away_matches_seen": 5, "h2h_match_count": 0},
        ])

        coverage = summarize_competition_feature_coverage(history, features)

        self.assertEqual([row["competition"] for row in coverage], ["PL", "PD"])
        self.assertEqual(coverage[0]["fixtures"], 2)
        self.assertEqual(coverage[0]["home_history_pct"], 50.0)
        self.assertEqual(coverage[0]["away_history_pct"], 50.0)
        self.assertEqual(coverage[0]["home_three_plus_pct"], 50.0)
        self.assertEqual(coverage[0]["h2h_history_pct"], 50.0)
        self.assertEqual(coverage[1]["home_history_pct"], 100.0)
        self.assertEqual(coverage[1]["away_three_plus_pct"], 100.0)
        self.assertEqual(coverage[1]["h2h_history_pct"], 0.0)

    def test_competition_coverage_handles_empty_and_missing_competition(self):
        self.assertEqual(
            summarize_competition_feature_coverage(pd.DataFrame(), pd.DataFrame()),
            [],
        )
        coverage = summarize_competition_feature_coverage(
            pd.DataFrame([{"home_team": "A", "away_team": "B"}]),
            pd.DataFrame([{
                "home_matches_seen": 0,
                "away_matches_seen": 0,
                "h2h_match_count": 0,
            }]),
        )
        self.assertEqual(coverage[0]["competition"], "UNKNOWN")
        self.assertEqual(coverage[0]["fixtures"], 1)
        self.assertEqual(coverage[0]["home_history_pct"], 0.0)


    def test_legacy_context_builds_fallback_profiles_once_for_repeated_fixtures(self):
        history = pd.DataFrame([
            {
                "home_team": "Alpha FC",
                "away_team": "Beta FC",
                "home_goals": 2,
                "away_goals": 1,
                "utc_date": pd.Timestamp("2025-01-01T15:00:00Z"),
            },
        ])
        context = {"history": history, "feature_columns": None}
        with patch("predict.utils._build_profiles_from_history", wraps=__import__(
            "predict.utils", fromlist=["_build_profiles_from_history"]
        )._build_profiles_from_history) as profile_builder:
            build_fixture_features("Alpha FC", "Beta FC", context)
            build_fixture_features("Alpha FC", "Beta FC", context)
        self.assertEqual(profile_builder.call_count, 1)
        self.assertIn("team_profiles", context)
        self.assertIn("h2h_profiles", context)

    def test_goal_rate_bias_is_shrunk_and_capped_from_holdout_residuals(self):
        actual = [1.5] * 20
        predicted = [1.0] * 20
        self.assertAlmostEqual(_estimate_goal_rate_bias(actual, predicted), 0.325)

        self.assertEqual(_estimate_goal_rate_bias([2.0] * 10, [1.0] * 10), 0.0)
        self.assertEqual(_estimate_goal_rate_bias([3.0] * 20, [1.0] * 20), 0.35)

    def test_legacy_h2h_index_is_cached_and_normalized(self):
        from .utils import build_fixture_features

        history = pd.DataFrame([{
            "home_team": "Alpha FC", "away_team": "Beta FC",
            "home_goals": 1, "away_goals": 0,
            "utc_date": pd.Timestamp("2025-01-01T15:00:00Z"),
        }])
        context = {
            "history": history,
            "lookback": 8,
            "feature_columns": None,
            "league_home_goals": 1.4,
            "league_away_goals": 1.1,
            "team_profiles": {},
            "h2h_profiles": {
                ("Alpha FC", "Beta FC"): [{
                    "home_team": "alpha", "away_team": "beta",
                    "home_goals": 1, "away_goals": 0,
                }],
            },
            "elo_ratings": {},
        }
        build_fixture_features("Alpha FC", "Beta FC", context)
        cached_index = context["_normalized_h2h_profiles"]
        build_fixture_features("Beta FC", "Alpha FC", context)
        self.assertIs(context["_normalized_h2h_profiles"], cached_index)
        self.assertIn(("alpha", "beta"), cached_index)
