from datetime import date, timedelta
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from predict.models import MatchPrediction
from predict.views import predictions_view


class PredictionsWindowAndLeagueFilterTests(TestCase):
    def setUp(self):
        self.today = date(2026, 11, 20)
        self.factory = RequestFactory()
        self.patches = [
            patch("predict.views.timezone.localdate", return_value=self.today),
            patch("predict.views.get_league_table", return_value=[]),
            patch("predict.views.get_team_metadata", side_effect=lambda name, code=None: {"shortName": name, "crest": ""}),
            patch("predict.views.get_team_recent_form", return_value=[]),
            patch("predict.views.get_cached_kickoff_time", return_value="12:00"),
            patch("predict.views.resolve_competition_logo", return_value=""),
            patch("predict.views.build_match_detail_url", return_value="/match/"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.render_mock = patch(
            "predict.views.render",
            side_effect=lambda request, template, context: HttpResponse("ok"),
        )
        self.render_mock_object = self.render_mock.start()
        self.addCleanup(self.render_mock.stop)

    def add_prediction(self, match_id, competition, day):
        return MatchPrediction.objects.create(
            match_id=match_id,
            home_team=f"{match_id} Home",
            away_team=f"{match_id} Away",
            match_date=day,
            competition=competition,
            predicted_home_goals=1,
            predicted_away_goals=0,
        )

    def get_context(self, params=None):
        request = self.factory.get("/", params or {})
        response = predictions_view(request)
        self.assertEqual(response.status_code, 200)
        return self.render_mock_object.call_args.args[2]

    def test_date_outside_seven_day_window_is_rejected(self):
        self.add_prediction("today", "PL", self.today)
        self.add_prediction("eighth-day", "PL", self.today + timedelta(days=7))

        context = self.get_context({"match_date": (self.today + timedelta(days=7)).isoformat()})

        self.assertEqual(context["selected_date"], self.today.isoformat())
        self.assertEqual([row["home_team"] for row in context["predictions"]], ["today Home"])
        self.assertEqual(context["prediction_window_end"], (self.today + timedelta(days=6)).isoformat())

    def test_competition_filter_only_returns_selected_league(self):
        self.add_prediction("premier", "PL", self.today)
        self.add_prediction("spain", "PD", self.today)

        context = self.get_context({"competition": "PD", "match_date": self.today.isoformat()})

        self.assertEqual(context["selected_competition"], "PD")
        self.assertEqual([row["competition_code"] for row in context["predictions"]], ["PD"])

    def test_all_leagues_are_grouped_in_configured_rank_order(self):
        self.add_prediction("spain", "PD", self.today)
        self.add_prediction("champions", "CL", self.today)
        self.add_prediction("premier", "PL", self.today)

        context = self.get_context({"match_date": self.today.isoformat()})

        self.assertEqual(
            [row["competition_code"] for row in context["predictions"]],
            ["PL", "CL", "PD"],
        )
        self.assertEqual(context["selected_competition"], "")
        self.assertGreaterEqual(len(context["competition_options"]), 3)
