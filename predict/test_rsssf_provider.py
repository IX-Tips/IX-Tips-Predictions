import os
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from predict.providers import supports_rsssf

from predict.constants import COMPETITIONS, COMPETITION_PROVIDERS
from predict.rsssf import (
    RSSSF_COMPETITIONS,
    _page_url,
    parse_rsssf_text,
    season_year_for_date,
)


class RSSSFAfricanFixtureTests(TestCase):
    def test_parses_results_and_upcoming_fixtures_with_explicit_dates(self):
        source = """
Round 1
[Sep 18]
  Rahimo FC              1-0  AS Douanes
  Vitesse FC              -   FocuSport FC
Round 2
[Jan 6]
  AS Douanes              -   Rahimo FC
"""
        matches = parse_rsssf_text(source, "BFA", 2026)

        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["utcDate"], "2026-09-18")
        self.assertEqual(matches[0]["status"], "FINISHED")
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 1, "away": 0})
        self.assertEqual(matches[1]["status"], "TIMED")
        self.assertIsNone(matches[1]["score"]["fullTime"]["home"])
        self.assertEqual(matches[2]["utcDate"], "2027-01-06")

    def test_round_without_date_does_not_reuse_previous_round_date(self):
        source = """
Round 1
[Oct 10]
  Home FC                 1-0  Away FC
Round 2
  Other Home               -   Other Away
"""
        matches = parse_rsssf_text(source, "BEN", 2026)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["utcDate"], "2026-10-10")

    def test_tba_rows_are_not_assigned_a_fabricated_date(self):
        source = """
Round 1
[tba]
  Inter Star               -   Le Messager Ngozi
"""
        self.assertEqual(parse_rsssf_text(source, "BDI", 2026), [])

    def test_season_url_mapping_and_date_rollover(self):
        self.assertEqual(
            _page_url("ANG", 2026),
            "https://www.rsssf.org/tablesa/ango2027.html",
        )
        self.assertEqual(
            _page_url("LBR", 2026),
            "https://www.rsssf.org/tablesl/liber2027.html",
        )
        self.assertEqual(season_year_for_date(date(2026, 11, 24)), 2026)
        self.assertEqual(season_year_for_date(date(2027, 1, 6)), 2026)

    def test_html_page_markup_is_stripped_before_parsing(self):
        source = """<!doctype html><html><body><pre>
Round 1
[Sep 18]
  Rahimo FC              1-0  AS Douanes
  Vitesse FC              -   FocuSport FC
</pre></body></html>"""
        matches = parse_rsssf_text(source, "BFA", 2026)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["homeTeam"]["name"], "Rahimo FC")
        self.assertEqual(matches[1]["status"], "TIMED")

    def test_actual_rsssf_spacing_and_ground_note_are_parsed(self):
        source = """Round 1 [Aug 21]
  FC Cabinda               0-3 Recreativo do Libolo     [at Uíge]
"""
        matches = parse_rsssf_text(source, "ANG", 2026)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["homeTeam"]["name"], "FC Cabinda")
        self.assertEqual(matches[0]["awayTeam"]["name"], "Recreativo do Libolo")
        self.assertEqual(matches[0]["score"]["fullTime"], {"home": 0, "away": 3})

    def test_round_header_with_inline_date_is_supported(self):
        source = """Round 3 [Oct 25]
  Home FC                 2-1  Away FC
"""
        matches = parse_rsssf_text(source, "ANG", 2026)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["utcDate"], "2026-10-25")

    def test_rsssf_source_is_disabled_unless_explicitly_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(supports_rsssf("ANG"))
        with patch.dict(os.environ, {"RSSSF_SOURCE_ENABLED": "true"}):
            self.assertTrue(supports_rsssf("ANG"))
            self.assertFalse(supports_rsssf("KEN"))

    def test_six_rsssf_competitions_are_registered(self):
        expected = {"ANG", "BEN", "BFA", "BDI", "LBR", "MTN"}
        self.assertEqual(set(RSSSF_COMPETITIONS), expected)
        self.assertTrue(expected.issubset(COMPETITIONS))
        self.assertTrue(all(COMPETITION_PROVIDERS[code] == "RSSSF" for code in expected))
