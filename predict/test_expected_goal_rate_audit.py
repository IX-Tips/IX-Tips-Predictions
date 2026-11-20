from datetime import date
from io import StringIO
import json

from django.core.management import call_command
from django.test import TestCase

from predict.models import MatchPrediction


class ExpectedGoalRateAuditTests(TestCase):
    def test_compares_rates_and_display_scores_on_same_completed_rows(self):
        MatchPrediction.objects.create(
            match_id="rate-001", home_team="Home A", away_team="Away A",
            match_date=date(2026, 11, 1), competition="PL",
            predicted_home_goals=1, predicted_away_goals=0,
            predicted_home_rate=1.4, predicted_away_rate=0.8,
            actual_home_goals=2, actual_away_goals=1, status="FINISHED",
        )
        MatchPrediction.objects.create(
            match_id="rate-002", home_team="Home B", away_team="Away B",
            match_date=date(2026, 11, 2), competition="PL",
            predicted_home_goals=0, predicted_away_goals=0,
            predicted_home_rate=None, predicted_away_rate=0.6,
            actual_home_goals=0, actual_away_goals=1, status="FINISHED",
        )
        out = StringIO()
        call_command("audit_expected_goal_rates", json=True, stdout=out)
        report = json.loads(out.getvalue())

        self.assertEqual(report["matches_with_actual_and_display_score"], 2)
        self.assertEqual(report["matches_with_both_expected_goal_rates"], 1)
        self.assertEqual(report["matches_missing_home_rate"], 1)
        self.assertEqual(report["matches_missing_away_rate"], 0)
        self.assertEqual(report["rate_coverage"], 0.5)
        home = report["expected_goal_rate_metrics"]["home"]
        away = report["expected_goal_rate_metrics"]["away"]
        self.assertEqual(home["mean_predicted_rate"], 1.4)
        self.assertEqual(home["mean_actual_goals"], 2.0)
        self.assertEqual(home["rate_mae"], 0.6)
        self.assertEqual(home["rate_bias_pred_minus_actual"], -0.6)
        self.assertEqual(away["mean_predicted_rate"], 0.8)
        self.assertEqual(away["rate_mae"], 0.2)
        self.assertEqual(report["by_competition"]["PL"]["matches"], 2)

    def test_empty_dataset_returns_null_metrics(self):
        out = StringIO()
        call_command("audit_expected_goal_rates", json=True, stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report["matches_with_actual_and_display_score"], 0)
        self.assertIsNone(report["expected_goal_rate_metrics"]["home"]["rate_mae"])
