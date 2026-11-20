import json
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from predict.models import MatchPrediction


class PredictionAccuracyAuditTests(TestCase):
    def test_audit_reports_goal_error_and_result_metrics(self):
        MatchPrediction.objects.create(
            match_id="audit-fixture-001",
            home_team="Home FC",
            away_team="Away FC",
            match_date=date(2026, 11, 1),
            competition="PL",
            predicted_home_goals=2,
            predicted_away_goals=0,
            actual_home_goals=1,
            actual_away_goals=0,
            status="FINISHED",
        )

        output = StringIO()
        call_command("audit_prediction_accuracy", json=True, stdout=output)
        report = json.loads(output.getvalue())

        self.assertEqual(report["matches_evaluated"], 1)
        self.assertEqual(report["home_goals_mae"], 1.0)
        self.assertEqual(report["away_goals_mae"], 0.0)
        self.assertEqual(report["combined_goals_mae"], 0.5)
        self.assertEqual(report["goal_difference_mae"], 1.0)
        self.assertEqual(report["exact_score_accuracy"], 0.0)
        self.assertEqual(report["result_accuracy"], 1.0)
        self.assertEqual(report["by_predicted_goal_margin"]["2-3"]["matches"], 1)

    def test_audit_excludes_unfinished_and_incomplete_results(self):
        MatchPrediction.objects.create(
            match_id="audit-fixture-002",
            home_team="Home FC",
            away_team="Away FC",
            match_date=date(2026, 11, 2),
            competition="PL",
            predicted_home_goals=1,
            predicted_away_goals=0,
            status="TIMED",
        )
        output = StringIO()
        call_command("audit_prediction_accuracy", json=True, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["matches_evaluated"], 0)
