from datetime import date, timedelta
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from predict.models import MatchPrediction, TopPick
from predict.views import predictions_view
from predict.utils import save_predictions, _build_feature_row, _new_team_profile, _update_team_profile


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
        self.assertEqual([row["home_team"] for row in context["predictions"]], ["Today Home"])
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


    def test_past_dates_remain_selectable(self):
        past_day = self.today - timedelta(days=30)
        self.add_prediction("historic", "PL", past_day)

        context = self.get_context({"match_date": past_day.isoformat()})

        self.assertEqual(context["selected_date"], past_day.isoformat())
        self.assertEqual([row["home_team"] for row in context["predictions"]], ["Historic Home"])


    def test_default_date_prefers_nearest_upcoming_over_old_history(self):
        self.add_prediction("old", "PL", self.today - timedelta(days=20))
        self.add_prediction("next", "PL", self.today + timedelta(days=2))

        context = self.get_context()

        self.assertEqual(context["selected_date"], (self.today + timedelta(days=2)).isoformat())

    def test_duplicate_provider_rows_render_once_without_rewriting_snapshots(self):
        day = self.today + timedelta(days=1)
        first = MatchPrediction.objects.create(
            match_id="provider-one-123",
            home_team="Manchester City",
            away_team="Fulham FC",
            match_date=day,
            competition="PL",
            predicted_home_goals=2,
            predicted_away_goals=0,
        )
        MatchPrediction.objects.create(
            match_id="provider-two-456",
            home_team="Manchester City FC",
            away_team="Fulham",
            match_date=day,
            competition="PL",
            predicted_home_goals=1,
            predicted_away_goals=0,
        )

        context = self.get_context({"match_date": day.isoformat()})

        self.assertEqual(len(context["predictions"]), 1)
        self.assertEqual(context["predictions"][0]["predicted_home_goals"], 2)
        first.refresh_from_db()
        self.assertEqual(first.predicted_home_goals, 2)

    def test_scheduled_prediction_reuses_fixture_across_provider_ids_and_aliases(self):
        day = self.today + timedelta(days=1)
        matches = [
            {
                "id": 101, "utcDate": f"{day.isoformat()}T15:30:00Z",
                "homeTeam": {"name": "Manchester City"},
                "awayTeam": {"name": "Fulham FC"},
                "predicted_home_goals": 2, "predicted_away_goals": 0,
            },
            {
                "id": 909, "utcDate": f"{day.isoformat()}T15:30:00Z",
                "homeTeam": {"name": "Man City"},
                "awayTeam": {"name": "Fulham"},
                "predicted_home_goals": 1, "predicted_away_goals": 1,
            },
        ]

        saved = save_predictions(
            matches, match_date=day.isoformat(), competition_code="PL",
        )

        self.assertEqual(len(MatchPrediction.objects.filter(match_date=day, competition="PL")), 1)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0].pk, saved[1].pk)
        self.assertEqual(saved[0].predicted_home_goals, 2)

    def test_feature_builder_resolves_team_and_h2h_profiles_by_alias(self):
        profiles = {"Manchester City": _new_team_profile()}
        _update_team_profile(profiles["Manchester City"], 2, 0, "home", self.today)
        h2h = {
            tuple(sorted(("Manchester City", "Fulham"))): [{
                "home_team": "Manchester City",
                "away_team": "Fulham",
                "home_goals": 3,
                "away_goals": 1,
            }]
        }

        row = _build_feature_row(
            "Man City", "Fulham",
            profiles, h2h,
            {"home_goals": 1.5, "away_goals": 1.0},
            8, None,
            {"Manchester City": 1710, "Fulham": 1500},
        )

        self.assertGreater(row["home_recent_scored"], 1.5)
        self.assertEqual(row["elo_gap"], 210)
        self.assertEqual(row["h2h_match_count"], 1)

    def test_sidebar_reads_only_the_default_stored_top_pick_variant(self):
        self.add_prediction("premier", "PL", self.today)
        TopPick.objects.create(
            match_date=self.today,
            home_team="premier Home",
            away_team="premier Away",
            variant="1",
            tip="1",
            confidence=62,
        )
        TopPick.objects.create(
            match_date=self.today,
            home_team="other Home",
            away_team="other Away",
            variant="2",
            tip="2",
            confidence=99,
        )

        context = self.get_context({"match_date": self.today.isoformat()})

        stored = [
            pick
            for picks in context["top_predictions"].values()
            for pick in picks
        ]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["tip"], "1")
