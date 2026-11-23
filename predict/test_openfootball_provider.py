from unittest import TestCase
from datetime import date

from predict.constants import COMPETITIONS, COMPETITION_PROVIDERS, OPENFOOTBALL_COMPETITIONS
from predict.openfootball import parse_openfootball_text, season_year_for_date


class OpenFootballAfricaParserTests(TestCase):
    def test_parses_finished_results_and_date_only_fixtures(self):
        source = """
= Kenya | Premier League 2025/26

  Fri Sep 19 2025
           Tusker FC              v KCB FC                 0-2
  Sat Sep 20 2025
           Kariobangi Sharks FC   v Bandari FC             0-0
           Gor Mahia FC           v Bidco United FC
"""
        matches = parse_openfootball_text(source, "KEN", 2025)

        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["utcDate"], "2025-09-19")
        self.assertEqual(matches[0]["homeTeam"]["name"], "Tusker FC")
        self.assertEqual(matches[0]["awayTeam"]["name"], "KCB FC")
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 0, "away": 2})
        self.assertEqual(matches[0]["status"], "FINISHED")
        self.assertEqual(matches[2]["status"], "TIMED")
        self.assertIsNone(matches[2]["score"]["fullTime"]["home"])


    def test_strips_kickoff_times_from_home_team_names(self):
        source = """
  Sat Sep 20 2025
      20:00  Tala'ea El Gaish SC     v Al Masry Club            0-2
      17:30  Marumo Gallants         v Kaizer Chiefs            1-2
             Marumo Gallants         v AmaZulu FC               1-0
"""
        matches = parse_openfootball_text(source, "EGY", 2025)

        self.assertEqual(matches[0]["homeTeam"]["name"], "Tala'ea El Gaish SC")
        self.assertEqual(matches[0]["awayTeam"]["name"], "Al Masry Club")
        self.assertEqual(matches[1]["homeTeam"]["name"], "Marumo Gallants")
        self.assertEqual(matches[1]["awayTeam"]["name"], "Kaizer Chiefs")
        self.assertEqual(matches[2]["homeTeam"]["name"], "Marumo Gallants")

    def test_postponed_matches_are_not_marked_finished(self):
        source = """
  Sat Sep 20 2025
           Home FC                v Away FC                postponed
"""
        matches = parse_openfootball_text(source, "KEN", 2025)
        self.assertEqual(matches[0]["status"], "POSTPONED")
        self.assertEqual(matches[0]["awayTeam"]["name"], "Away FC")
        self.assertIsNone(matches[0]["score"]["fullTime"]["home"])

    def test_african_competitions_are_routed_to_openfootball(self):
        self.assertEqual(set(OPENFOOTBALL_COMPETITIONS), {
            "ALG", "EGY", "GHA", "KEN", "MWI", "MAR", "NGA",
            "RWA", "SEN", "ZAF", "TZA", "UGA", "ZMB", "ZWE",
        })
        # Nigeria and Kenya have dedicated federation sources; the remaining
        # African leagues in this catalogue continue to use OpenFootball.
        self.assertEqual(COMPETITION_PROVIDERS["NGA"], "NPFL")
        self.assertEqual(COMPETITION_PROVIDERS["KEN"], "FKF")
        self.assertTrue(all(
            COMPETITION_PROVIDERS[code] == "OF"
            for code in OPENFOOTBALL_COMPETITIONS
            if code not in {"NGA", "KEN"}
        ))
        self.assertTrue(all(code in COMPETITIONS for code in OPENFOOTBALL_COMPETITIONS))

    def test_calendar_year_and_split_year_season_resolution(self):
        self.assertEqual(season_year_for_date("MWI", date(2025, 11, 20)), 2025)
        self.assertEqual(season_year_for_date("ZWE", date(2026, 2, 10)), 2026)
        self.assertEqual(season_year_for_date("KEN", date(2026, 2, 10)), 2025)
        self.assertEqual(season_year_for_date("KEN", date(2026, 8, 10)), 2026)
