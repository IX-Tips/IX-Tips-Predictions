from unittest import TestCase
from unittest.mock import patch

from predict.constants import _normalize_football_logo_name


class FootballLogoNameNormalizationTests(TestCase):
    def test_strips_common_provider_club_suffixes(self):
        self.assertEqual(_normalize_football_logo_name("Kaizer Chiefs FC"), "kaizer chiefs")
        self.assertEqual(_normalize_football_logo_name("Mamelodi Sundowns SC"), "mamelodi sundowns")
        self.assertEqual(_normalize_football_logo_name("AFC Leopards"), "leopards")

    def test_preserves_meaningful_name_tokens(self):
        self.assertEqual(_normalize_football_logo_name("Manchester United"), "manchester united")
        self.assertEqual(_normalize_football_logo_name("Young Africans SC"), "young africans")


class ApiTeamLogoFallbackTests(TestCase):
    def test_prefers_provider_crest(self):
        from predict.api_views import _team_logo

        with patch("predict.api_views.get_football_team_logo_url", return_value="catalog.png"):
            with patch("predict.api_views.country_flag_url", return_value="flag.png"):
                self.assertEqual(
                    _team_logo("Arsenal", "PL", {"crest": "provider.png"}),
                    "catalog.png",
                )

    def test_uses_catalogue_before_competition_flag(self):
        from predict.api_views import _team_logo

        with patch("predict.api_views.get_football_team_logo_url", return_value="catalog.png"):
            with patch("predict.api_views.country_flag_url", return_value="flag.png"):
                self.assertEqual(_team_logo("Arsenal", "PL", {}), "catalog.png")

    def test_uses_provider_crest_when_catalogue_has_no_match(self):
        from predict.api_views import _team_logo

        with patch("predict.api_views.get_football_team_logo_url", return_value=None):
            with patch("predict.api_views.country_flag_url", return_value="flag.png"):
                self.assertEqual(_team_logo("Arsenal", "PL", {"crest": "provider.png"}), "provider.png")

    def test_uses_flag_when_no_club_badge_is_available(self):
        from predict.api_views import _team_logo

        with patch("predict.api_views.get_football_team_logo_url", return_value=None):
            with patch("predict.api_views.country_flag_url", return_value="flag.png"):
                self.assertEqual(_team_logo("Unknown Club", "PL", {}), "flag.png")
