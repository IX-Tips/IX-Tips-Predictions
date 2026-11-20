import json
from collections import defaultdict

from django.core.management.base import BaseCommand
from predict.models import MatchPrediction


def _ratio(value, count):
    return round(value / count, 4) if count else None


class Command(BaseCommand):
    help = (
        "Compare saved expected-goal rates (Poisson lambda) with actual goals "
        "and displayed scorelines. Read-only; performs no inference or writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--competition")
        parser.add_argument("--min-date")
        parser.add_argument("--max-date")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        qs = MatchPrediction.objects.filter(
            status="FINISHED",
            actual_home_goals__isnull=False,
            actual_away_goals__isnull=False,
            predicted_home_goals__isnull=False,
            predicted_away_goals__isnull=False,
        )
        if options.get("competition"):
            qs = qs.filter(competition=options["competition"].upper())
        if options.get("min_date"):
            qs = qs.filter(match_date__gte=options["min_date"])
        if options.get("max_date"):
            qs = qs.filter(match_date__lte=options["max_date"])

        fields = (
            "competition", "match_id", "match_date",
            "predicted_home_goals", "predicted_away_goals",
            "predicted_home_rate", "predicted_away_rate",
            "actual_home_goals", "actual_away_goals",
        )
        rows = qs.order_by("match_date", "competition", "pk").values(*fields).iterator(
            chunk_size=2000
        )

        total = rate_complete = rate_missing_home = rate_missing_away = 0
        sums = defaultdict(float)
        comps = defaultdict(lambda: defaultdict(float))

        for row in rows:
            total += 1
            ph, pa = row["predicted_home_goals"], row["predicted_away_goals"]
            ah, aa = row["actual_home_goals"], row["actual_away_goals"]
            bucket = comps[row["competition"]]
            bucket["matches"] += 1

            # Displayed integer-scoreline metrics, repeated here so both
            # representations are evaluated over exactly the same records.
            for side, pred, actual in (("home", ph, ah), ("away", pa, aa)):
                sums[f"display_{side}_abs"] += abs(pred - actual)
                sums[f"display_{side}_bias"] += pred - actual
                bucket[f"display_{side}_abs"] += abs(pred - actual)
                bucket[f"display_{side}_bias"] += pred - actual

            hr, ar = row["predicted_home_rate"], row["predicted_away_rate"]
            if hr is None:
                rate_missing_home += 1
            if ar is None:
                rate_missing_away += 1
            if hr is None or ar is None:
                bucket["rate_missing"] += 1
                continue

            rate_complete += 1
            sums["rate_complete_display_home_abs"] += abs(ph - ah)
            sums["rate_complete_display_away_abs"] += abs(pa - aa)
            for side, pred, actual in (("home", float(hr), ah), ("away", float(ar), aa)):
                error = abs(pred - actual)
                bias = pred - actual
                sums[f"rate_{side}_sum"] += pred
                sums[f"rate_{side}_abs"] += error
                sums[f"rate_{side}_bias"] += bias
                bucket[f"rate_{side}_sum"] += pred
                bucket[f"rate_{side}_abs"] += error
                bucket[f"rate_{side}_bias"] += bias

        rate_report = {}
        for side in ("home", "away"):
            rate_report[side] = {
                "mean_predicted_rate": _ratio(sums[f"rate_{side}_sum"], rate_complete),
                "mean_actual_goals": None,
                "rate_mae": _ratio(sums[f"rate_{side}_abs"], rate_complete),
                "rate_bias_pred_minus_actual": _ratio(sums[f"rate_{side}_bias"], rate_complete),
                "displayed_score_mae_same_sample": _ratio(
                    sums[f"rate_complete_display_{side}_abs"], rate_complete
                ),
            }

        # Calculate actual means on the same complete-rate subset by accumulating
        # rate + bias = actual; this avoids a second database query.
        for side in ("home", "away"):
            rate_report[side]["mean_actual_goals"] = round(
                rate_report[side]["mean_predicted_rate"]
                - rate_report[side]["rate_bias_pred_minus_actual"], 4
            ) if rate_complete else None

        by_competition = {}
        for code, c in sorted(comps.items()):
            n = int(c["matches"])
            rn = n - int(c["rate_missing"])
            item = {
                "matches": n,
                "complete_rate_pairs": rn,
                "missing_rate_pairs": int(c["rate_missing"]),
            }
            for side in ("home", "away"):
                item[f"{side}_rate_mean"] = _ratio(c[f"rate_{side}_sum"], rn)
                item[f"{side}_rate_mae"] = _ratio(c[f"rate_{side}_abs"], rn)
                item[f"{side}_rate_bias"] = _ratio(c[f"rate_{side}_bias"], rn)
                item[f"{side}_display_mae"] = _ratio(c[f"display_{side}_abs"], n)
            by_competition[code] = item

        report = {
            "matches_with_actual_and_display_score": total,
            "matches_with_both_expected_goal_rates": rate_complete,
            "matches_missing_home_rate": rate_missing_home,
            "matches_missing_away_rate": rate_missing_away,
            "rate_coverage": _ratio(rate_complete, total),
            "expected_goal_rate_metrics": rate_report,
            "by_competition": by_competition,
            "read_only": True,
        }

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
            return

        self.stdout.write("IX-TIPS EXPECTED-GOAL RATE AUDIT")
        self.stdout.write("=" * 36)
        self.stdout.write(f"Completed matches with displayed scores: {total}")
        self.stdout.write(f"Matches with both expected-goal rates: {rate_complete}")
        self.stdout.write(f"Missing home rates: {rate_missing_home}")
        self.stdout.write(f"Missing away rates: {rate_missing_away}")
        self.stdout.write(f"Rate coverage: {report['rate_coverage']}")
        for side in ("home", "away"):
            metrics = rate_report[side]
            self.stdout.write(
                f"{side.title()}: mean rate={metrics['mean_predicted_rate']} "
                f"actual mean={metrics['mean_actual_goals']} "
                f"rate MAE={metrics['rate_mae']} "
                f"rate bias={metrics['rate_bias_pred_minus_actual']} "
                f"display MAE={metrics['displayed_score_mae_same_sample']}"
            )
        self.stdout.write("BY COMPETITION")
        self.stdout.write(json.dumps(by_competition, indent=2, sort_keys=True))
        self.stdout.write("Read-only: no predictions, caches, models or training data changed.")
