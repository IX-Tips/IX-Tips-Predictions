from unittest.mock import patch

from django.test import SimpleTestCase

from predict.constants import get_team_metadata


class TeamLogoFallbackTests(SimpleTestCase):
    @patch("predict.constants.cache.get")
    def test_uses_competition_country_flag_when_provider_has_no_team_crest(self, cache_get):
        cache_get.return_value = {"shortName": "Example FC", "crest": None}
        metadata = get_team_metadata("Example FC", "TUR")
        self.assertEqual(metadata["crest"], "https://flagcdn.com/w160/tr.png")

    @patch("predict.constants.cache.get")
    def test_preserves_real_team_crest(self, cache_get):
        cache_get.return_value = {
            "shortName": "Example FC",
            "crest": "https://example.com/team.png",
        }
        metadata = get_team_metadata("Example FC", "TUR")
        self.assertEqual(metadata["crest"], "https://example.com/team.png")

    @patch("predict.constants.cache.get")
    def test_keeps_crest_empty_when_no_competition_is_known(self, cache_get):
        cache_get.return_value = {"shortName": "Example FC", "crest": None}
        metadata = get_team_metadata("Example FC")
        self.assertIsNone(metadata["crest"])
