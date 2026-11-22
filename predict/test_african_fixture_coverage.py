from datetime import date
from unittest.mock import patch

from django.test import TestCase

from predict.management.commands.african_fixture_coverage import build_report


class AfricanFixtureCoverageReportTests(TestCase):
    @patch("predict.management.commands.african_fixture_coverage.get_provider_diagnostic", return_value=None)
    @patch("predict.management.commands.african_fixture_coverage.provider_configuration", return_value=(True, "test"))
    @patch("predict.management.commands.african_fixture_coverage._provider_order", return_value=["OF"])
    @patch("predict.management.commands.african_fixture_coverage.get_provider", return_value="OF")
    def test_report_has_african_competitions_and_diagnostic_caveat(self, primary, order, config, diagnostic):
        report = build_report(today=date(2099, 1, 1))
        codes = {row["code"] for row in report["competitions"]}
        self.assertIn("KEN", codes)
        self.assertIn("NGA", codes)
        self.assertIn("not raw provider fixtures", report["note"])
        kenya = next(row for row in report["competitions"] if row["code"] == "KEN")
        self.assertEqual(kenya["primary"], "OF")
        self.assertEqual(kenya["provider_chain"][0]["diagnostic"], None)
        self.assertEqual(kenya["upcoming_prediction_records"], 0)

    @patch("predict.management.commands.african_fixture_coverage.get_provider_diagnostic", return_value={"status": "error", "last_checked": "old"})
    @patch("predict.management.commands.african_fixture_coverage.provider_configuration", return_value=(False, "API_KEY"))
    @patch("predict.management.commands.african_fixture_coverage._provider_order", return_value=["OF", "AF"])
    @patch("predict.management.commands.african_fixture_coverage.get_provider", return_value="OF")
    def test_reports_unconfigured_fallback_and_cached_error(self, primary, order, config, diagnostic):
        report = build_report(today=date(2099, 1, 1))
        kenya = next(row for row in report["competitions"] if row["code"] == "KEN")
        self.assertEqual([item["provider"] for item in kenya["provider_chain"]], ["OF", "AF"])
        self.assertFalse(kenya["provider_chain"][1]["configured"])
        self.assertEqual(kenya["provider_chain"][1]["diagnostic"]["status"], "error")
