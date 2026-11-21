from django.test import SimpleTestCase

from predict.rsssf_audit_parser import parse_rsssf_text


class RSSSFParserAuditTests(SimpleTestCase):
    def test_extracts_scheduled_and_completed_fixture_lines(self):
        source = """
Round 3
23.10. 2026  Gor Mahia - AFC Leopards
24.10.2026  Tusker 2-1 Bandari
"""
        parsed = parse_rsssf_text(source, season_year=2026)
        self.assertEqual(len(parsed["fixtures"]), 2)
        first, second = parsed["fixtures"]
        self.assertEqual((first["date"], first["home"], first["away"], first["status"]),
                         ("2026-10-23", "Gor Mahia", "AFC Leopards", "scheduled"))
        self.assertEqual(second["score"], (2, 1))
        self.assertEqual(second["status"], "finished")

    def test_deduplicates_exact_fixture_and_skips_malformed_date(self):
        source = """
10.11.2026  Team A - Team B
10.11.2026  Team A - Team B
32.11.2026  Team C - Team D
"""
        parsed = parse_rsssf_text(source)
        self.assertEqual(len(parsed["fixtures"]), 1)
        self.assertEqual(parsed["skipped"][0]["reason"], "invalid_date")

    def test_does_not_guess_unparseable_lines(self):
        parsed = parse_rsssf_text("Oct 2026: fixtures to be announced")
        self.assertEqual(parsed["fixtures"], [])
