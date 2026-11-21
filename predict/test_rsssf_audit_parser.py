from django.test import SimpleTestCase

from predict.rsssf_audit_parser import parse_rsssf_text


class RSSSFParserAuditTests(SimpleTestCase):
    def test_parses_rsssf_month_date_blocks_and_results(self):
        # Representative text layout transcribed from the public 2026/27 page.
        source = """
Round 1
[Oct 10]
Mathare United 0-0 APS Bomet
Posta Rangers 0-1 Shabana
[Oct 11]
Ulinzi Stars 0-2 Tusker
3K 2-2 Mara Sugar
"""
        parsed = parse_rsssf_text(source, season_year=2026)
        fixtures = parsed["fixtures"]
        self.assertEqual(len(fixtures), 4)
        self.assertEqual(
            (fixtures[0]["date"], fixtures[0]["home"], fixtures[0]["away"]),
            ("2026-10-10", "Mathare United", "APS Bomet"),
        )
        self.assertEqual(fixtures[0]["score"], (0, 0))
        self.assertEqual(fixtures[0]["round"], "Round 1")
        self.assertEqual(fixtures[2]["date"], "2026-10-11")
        self.assertEqual(fixtures[2]["status"], "finished")

    def test_parses_scheduled_fixture_under_known_date(self):
        parsed = parse_rsssf_text(
            """Round 8
[Nov 22]
Gor Mahia - AFC Leopards""",
            season_year=2026,
        )
        self.assertEqual(len(parsed["fixtures"]), 1)
        fixture = parsed["fixtures"][0]
        self.assertEqual(fixture["date"], "2026-11-22")
        self.assertIsNone(fixture["score"])
        self.assertEqual(fixture["status"], "scheduled")

    def test_rolls_season_dates_into_following_calendar_year(self):
        parsed = parse_rsssf_text(
            """Round 20
[Jan 8]
Bandari 1-0 Tusker""",
            season_year=2026,
        )
        self.assertEqual(parsed["fixtures"][0]["date"], "2027-01-08")

    def test_ignores_tba_rows_without_inventing_a_date(self):
        parsed = parse_rsssf_text(
            """Round 7
[tba]
Nairobi United - Mara Sugar
Tusker - Shabana""",
            season_year=2026,
        )
        self.assertEqual(parsed["fixtures"], [])

    def test_numeric_date_rows_and_exact_deduplication(self):
        source = """
23.10.2026 Gor Mahia - AFC Leopards
23.10.2026 Gor Mahia - AFC Leopards
24.10.2026 Tusker 2-1 Bandari
"""
        parsed = parse_rsssf_text(source, season_year=2026)
        self.assertEqual(len(parsed["fixtures"]), 2)
        self.assertEqual(parsed["fixtures"][1]["score"], (2, 1))

    def test_skips_invalid_dates_and_unparseable_text(self):
        parsed = parse_rsssf_text(
            """32.11.2026 Team A - Team B
Oct 2026: fixtures to be announced"""
        )
        self.assertEqual(parsed["fixtures"], [])
        self.assertEqual(parsed["skipped"][0]["reason"], "invalid_date")
