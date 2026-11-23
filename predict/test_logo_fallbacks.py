from unittest.mock import patch

from django.test import SimpleTestCase

from predict.constants import get_team_metadata


class TeamLogoFallbackTests(SimpleTestCase):
    @patch("predict.constants.cache.get")
    def test_uses_competition_country_flag_when_provider_has_no_team_crest(self, cache_get):
        cache_get.return_value = {"shortName": "Example FC", "crest": None}
        with patch("predict.constants.cache.set"):
            metadata = get_team_metadata("Example FC", "TUR")
        self.assertEqual(metadata["crest"], "https://flagcdn.com/w160/tr.png")

    @patch("predict.constants.cache.get")
    def test_preserves_real_team_crest(self, cache_get):
        cache_get.return_value = {
            "shortName": "Example FC",
            "crest": "https://example.com/team.png",
        }
        with patch("predict.constants.cache.set"):
            metadata = get_team_metadata("Example FC", "TUR")
        self.assertEqual(metadata["crest"], "https://example.com/team.png")

    @patch("predict.constants.cache.get")
    def test_does_not_reuse_a_cached_country_flag_without_a_competition(self, cache_get):
        cache_get.return_value = {
            "shortName": "Example FC",
            "crest": "https://flagcdn.com/w160/tr.png",
            "logo_source": "competition-country-flag",
        }
        with patch("predict.constants.get_football_team_logo_url", return_value=None):
            metadata = get_team_metadata("Example FC")
        self.assertFalse(metadata.get("crest"))

    @patch("predict.constants.cache.get")
    def test_keeps_crest_empty_when_no_competition_is_known(self, cache_get):
        cache_get.return_value = {"shortName": "Example FC", "crest": None}
        metadata = get_team_metadata("Example FC")
        self.assertIsNone(metadata["crest"])
