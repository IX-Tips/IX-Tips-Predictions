from unittest import TestCase

from predict.management.commands.audit_sportsevents_african_tournaments import (
    _matching_tournaments,
)
from predict.management.commands.audit_sportsevents_fixture_feeds import _candidates


class SportsEventsAuditTests(TestCase):
    def test_catalogue_audit_matches_season_qualified_aliases(self):
        rows = [{"id": 151263, "name": "Egyptian Premier League 2026/2027"}]
        self.assertEqual(
            [row["id"] for row in _matching_tournaments("EGY", rows)],
            [151263],
        )

    def test_fixture_audit_returns_both_south_africa_candidates_for_comparison(self):
        rows = [
            {"id": 9146, "name": "Betway Premiership"},
            {"id": 151664, "name": "Betway Premiership 2026/27"},
        ]
        self.assertEqual(
            [row["id"] for row in _candidates("ZAF", rows)],
            [9146, 151664],
        )

    def test_fixture_audit_matches_each_new_alias(self):
        cases = [
            ("GHA", 151267, "Ghana Premier League 2026/2027"),
            ("MAR", 151721, "Botola Pro D1 2026/2027"),
            ("MTN", 151640, "FFRIM Super D1 2026/2027"),
            ("MWI", 3324, "FDH Bank Premiership 2026/2027"),
            ("SEN", 171201, "Ligue 1 Sénégal 2026/2027"),
            ("TZA", 151680, "NBC Premier League 2026/27"),
        ]
        for code, tournament_id, name in cases:
            with self.subTest(code=code):
                self.assertEqual(
                    [row["id"] for row in _candidates(
                        code, [{"id": tournament_id, "name": name}]
                    )],
                    [tournament_id],
                )
