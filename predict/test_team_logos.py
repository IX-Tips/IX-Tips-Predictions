from unittest import TestCase

from predict.constants import _normalize_football_logo_name


class FootballLogoNameNormalizationTests(TestCase):
    def test_strips_common_provider_club_suffixes(self):
        self.assertEqual(_normalize_football_logo_name("Kaizer Chiefs FC"), "kaizer chiefs")
        self.assertEqual(_normalize_football_logo_name("Mamelodi Sundowns SC"), "mamelodi sundowns")
        self.assertEqual(_normalize_football_logo_name("AFC Leopards"), "leopards")

    def test_preserves_meaningful_name_tokens(self):
        self.assertEqual(_normalize_football_logo_name("Manchester United"), "manchester united")
        self.assertEqual(_normalize_football_logo_name("Young Africans SC"), "young africans")
