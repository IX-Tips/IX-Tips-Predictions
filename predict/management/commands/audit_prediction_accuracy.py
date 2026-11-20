import json
from collections import defaultdict

from django.core.management.base import BaseCommand
from predict.models import MatchPrediction


def _result(home_goals, away_goals):
    if home_goals > away_goals:
        return "Home"
    if home_goals < away_goals:
        return "Away"
    return "Draw"


def _rate(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


class Command(BaseCommand):
    help = (
        "Audit saved pre-match forecasts against completed results. "
        "This command is read-only and performs no model inference or retraining."
    )

    def add_arguments(self, parser):
        parser.add_argument("--competition", help="Limit the report to one competition code.")
        parser.add_argument(
            "--min-date",
            help="Include matches on or after this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--max-date",
            help="Include matches on or before this date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON instead of a human-readable report.",
        )

    def handle(self, *args, **options):
        queryset = MatchPrediction.objects.filter(
            status="FINISHED",
            actual_home_goals__isnull=False,
            actual_away_goals__isnull=False,
            predicted_home_goals__isnull=False,
            predicted_away_goals__isnull=False,
        )
        if options.get("competition"):
            queryset = queryset.filter(competition=options["competition"].upper())
        if options.get("min_date"):
            queryset = queryset.filter(match_date__gte=options["min_date"])
        if options.get("max_date"):
            queryset = queryset.filter(match_date__lte=options["max_date"])

        rows = queryset.order_by("match_date", "competition").values(
            "competition",
            "predicted_home_goals",
            "predicted_away_goals",
            "actual_home_goals",
            "actual_away_goals",
        )

        total = 0
        exact = 0
        result_correct = 0
        home_abs_error = 0
        away_abs_error = 0
        total_abs_error = 0
        margin_abs_error = 0
        competitions = defaultdict(lambda: self._empty_bucket())
        margins = {
            "0": self._empty_bucket(),
            "1": self._empty_bucket(),
            "2-3": self._empty_bucket(),
            "4+": self._empty_bucket(),
        }

        for row in rows.iterator(chunk_size=2000):
            ph = row["predicted_home_goals"]
            pa = row["predicted_away_goals"]
            ah = row["actual_home_goals"]
            aa = row["actual_away_goals"]
            home_error = abs(ph - ah)
            away_error = abs(pa - aa)
            score_exact = ph == ah and pa == aa
            result_match = _result(ph, pa) == _result(ah, aa)
            margin = abs(ph - pa)
            margin_key = "0" if margin == 0 else "1" if margin == 1 else "2-3" if margin <= 3 else "4+"

            total += 1
            exact += int(score_exact)
            result_correct += int(result_match)
            home_abs_error += home_error
            away_abs_error += away_error
            total_abs_error += home_error + away_error
            margin_abs_error += abs((ph - pa) - (ah - aa))

            bucket = competitions[row["competition"]]
            self._add(bucket, home_error, away_error, abs((ph - pa) - (ah - aa)), score_exact, result_match)
            self._add(margins[margin_key], home_error, away_error, abs((ph - pa) - (ah - aa)), score_exact, result_match)

        summary = {
            "matches_evaluated": total,
            "home_goals_mae": _rate(home_abs_error, total),
            "away_goals_mae": _rate(away_abs_error, total),
            "combined_goals_mae": _rate(total_abs_error, total * 2),
            "goal_difference_mae": _rate(margin_abs_error, total),
            "exact_score_accuracy": _rate(exact, total),
            "result_accuracy": _rate(result_correct, total),
            "by_competition": {
                code: self._format_bucket(bucket)
                for code, bucket in sorted(competitions.items())
            },
            "by_predicted_goal_margin": {
                label: self._format_bucket(bucket)
                for label, bucket in margins.items()
            },
        }

        if options["json"]:
            self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
            return

        self.stdout.write("IX-TIPS PREDICTION ACCURACY AUDIT")
        self.stdout.write("=" * 36)
        self.stdout.write(f"Completed predictions evaluated: {total}")
        self.stdout.write(f"Home goals MAE:                 {summary['home_goals_mae']}")
        self.stdout.write(f"Away goals MAE:                 {summary['away_goals_mae']}")
        self.stdout.write(f"Combined goals MAE:             {summary['combined_goals_mae']}")
        self.stdout.write(f"Goal-difference MAE:            {summary['goal_difference_mae']}")
        self.stdout.write(f"Exact-score accuracy:           {summary['exact_score_accuracy']}")
        self.stdout.write(f"Match-result accuracy:          {summary['result_accuracy']}")
        self.stdout.write("")
        self.stdout.write("BY COMPETITION")
        for code, metrics in summary["by_competition"].items():
            self.stdout.write(
                f"{code}: n={metrics['matches']} result={metrics['result_accuracy']} "
                f"score={metrics['exact_score_accuracy']} "
                f"GD_MAE={metrics['goal_difference_mae']}"
            )
        self.stdout.write("")
        self.stdout.write("BY PREDICTED GOAL MARGIN")
        for label, metrics in summary["by_predicted_goal_margin"].items():
            self.stdout.write(
                f"{label}: n={metrics['matches']} result={metrics['result_accuracy']} "
                f"score={metrics['exact_score_accuracy']} "
                f"GD_MAE={metrics['goal_difference_mae']}"
            )
        self.stdout.write("")
        self.stdout.write(
            "Read-only audit: no predictions, model caches, or training data were changed."
        )

    @staticmethod
    def _empty_bucket():
        return {
            "matches": 0,
            "home_abs_error": 0,
            "away_abs_error": 0,
            "goal_difference_abs_error": 0,
            "exact_scores": 0,
            "correct_results": 0,
        }

    @staticmethod
    def _add(bucket, home_error, away_error, goal_difference_error, exact, result_correct):
        bucket["matches"] += 1
        bucket["home_abs_error"] += home_error
        bucket["away_abs_error"] += away_error
        bucket["goal_difference_abs_error"] += goal_difference_error
        bucket["exact_scores"] += int(exact)
        bucket["correct_results"] += int(result_correct)

    @staticmethod
    def _format_bucket(bucket):
        count = bucket["matches"]
        return {
            "matches": count,
            "home_goals_mae": _rate(bucket["home_abs_error"], count),
            "away_goals_mae": _rate(bucket["away_abs_error"], count),
            "goal_difference_mae": _rate(bucket["goal_difference_abs_error"], count),
            "exact_score_accuracy": _rate(bucket["exact_scores"], count),
            "result_accuracy": _rate(bucket["correct_results"], count),
        }
