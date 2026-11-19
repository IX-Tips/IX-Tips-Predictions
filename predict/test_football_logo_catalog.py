from unittest.mock import patch

from django.test import SimpleTestCase

from predict.constants import (
    get_football_competition_logo_url,
    FOOTBALL_COMPETITION_LOGO_SLUGS,
    get_football_team_logo_url,
    get_team_metadata,
)


class FootballLogoCatalogTests(SimpleTestCase):
    def test_resolves_team_logo_from_competition_country(self):
        url = get_football_team_logo_url("Liverpool FC", "PL")
        self.assertTrue(url.startswith("https://assets.football-logos.cc/logos/england/256x256/liverpool."))
        self.assertTrue(url.endswith(".png"))

    def test_resolves_competition_logo(self):
        url = get_football_competition_logo_url("PL")
        self.assertTrue(url.startswith("https://assets.football-logos.cc/logos/england/256x256/english-premier-league."))
        self.assertTrue(url.endswith(".png"))

    def test_every_configured_competition_has_a_catalog_logo(self):
        for competition_code in FOOTBALL_COMPETITION_LOGO_SLUGS:
            with self.subTest(competition_code=competition_code):
                self.assertIsNotNone(get_football_competition_logo_url(competition_code))

    def test_preserves_provider_crest_before_catalog_fallback(self):
        provider_crest = "https://provider.example/crest.png"
        with patch("predict.constants.cache.get", return_value={"shortName": "Liverpool", "crest": provider_crest}):
            metadata = get_team_metadata("Liverpool FC", "PL")
        self.assertEqual(metadata["crest"], provider_crest)

    def test_uses_catalog_team_logo_when_provider_has_no_crest(self):
        with patch("predict.constants.cache.get", return_value={"shortName": "Liverpool FC", "crest": None}):
            metadata = get_team_metadata("Liverpool FC", "PL")
        self.assertTrue(metadata["crest"].startswith("https://assets.football-logos.cc/logos/england/256x256/liverpool."))
        self.assertEqual(metadata["logo_source"], "football-logos.cc")

    def test_uses_country_flag_when_team_is_not_in_catalog(self):
        with patch("predict.constants.cache.get", return_value={"shortName": "Unknown FC", "crest": None}):
            metadata = get_team_metadata("Unknown FC", "TUR")
        self.assertEqual(metadata["crest"], "https://flagcdn.com/w160/tr.png")

    def test_ambiguous_global_team_name_is_not_guessed(self):
        self.assertIsNone(get_football_team_logo_url("Wimbledon", "CL"))
