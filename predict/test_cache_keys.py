from django.test import SimpleTestCase

from predict.cache_keys import CacheKeys


class FixtureMetadataCacheKeyTests(SimpleTestCase):
    def test_fixture_metadata_key_is_safe_for_memcached(self):
        key = CacheKeys.fixture_meta(
            "PL",
            "2026-12-12",
            "Ipswich Town FC",
            "Newcastle United FC",
        )

        self.assertLessEqual(len(key), 250)
        self.assertFalse(any(character.isspace() or ord(character) < 32 for character in key))

    def test_fixture_metadata_key_is_deterministic(self):
        args = ("PL", "2026-12-12", "Ipswich Town FC", "Newcastle United FC")
        self.assertEqual(CacheKeys.fixture_meta(*args), CacheKeys.fixture_meta(*args))

    def test_fixture_metadata_key_changes_when_fixture_changes(self):
        first = CacheKeys.fixture_meta("PL", "2026-12-12", "Liverpool FC", "Leeds United FC")
        second = CacheKeys.fixture_meta("PL", "2026-12-12", "Liverpool FC", "Chelsea FC")
        self.assertNotEqual(first, second)
