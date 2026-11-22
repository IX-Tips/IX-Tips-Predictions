from unittest import TestCase
from unittest.mock import Mock, patch

from predict.npfl import parse_fixture_page


class NPFLFixtureParserTests(TestCase):
    def setUp(self):
        self.html = """
        <table><tbody>
          <tr class="sp-row sp-post">
            <td class="data-date" itemprop="startDate"
                content="2026-11-25T16:00+01:00">Nov 25, 2026</td>
            <td class="data-event">Sporting Lagos FC vs Rangers International FC</td>
            <td class="data-time">16:00:53 4:00 pm</td>
          </tr>
          <tr class="sp-row sp-post">
            <td class="data-date" itemprop="startDate"
                content="2026-11-22T16:00+01:00">Nov 22, 2026</td>
            <td class="data-event">Enyimba FC vs Barau FC</td>
            <td class="data-time">2 - 1</td>
          </tr>
          <tr class="sp-row sp-post">
            <td class="data-date" itemprop="startDate"
                content="2026-11-28T16:00+01:00">Nov 28, 2026</td>
            <td class="data-event">Plateau United FC vs Ikorodu City FC</td>
            <td class="data-time">Postponed</td>
          </tr>
        </tbody></table>
        """

    def test_parses_upcoming_fixture_and_preserves_timezone(self):
        matches = parse_fixture_page(self.html)
        upcoming = next(item for item in matches if item["status"] == "TIMED")
        self.assertEqual(upcoming["utcDate"], "2026-11-25T16:00+01:00")
        self.assertEqual(upcoming["homeTeam"]["name"], "Sporting Lagos FC")
        self.assertEqual(upcoming["awayTeam"]["name"], "Rangers International FC")

    def test_parses_finished_score_without_treating_it_as_upcoming(self):
        matches = parse_fixture_page(self.html)
        finished = next(item for item in matches if item["status"] == "FINISHED")
        self.assertEqual(finished["score"]["fullTime"], {"home": 2, "away": 1})

    def test_postponed_fixture_is_not_marked_timed(self):
        matches = parse_fixture_page(self.html)
        postponed = next(item for item in matches if item["status"] == "POSTPONED")
        self.assertIsNone(postponed["score"]["fullTime"]["home"])

    def test_deduplicates_identical_rows(self):
        matches = parse_fixture_page(self.html + self.html)
        self.assertEqual(len(matches), 3)

    @patch("predict.npfl.cache.get", return_value=None)
    @patch("predict.npfl.cache.set")
    @patch("predict.npfl._robots_allow_fetch", return_value=True)
    @patch("predict.npfl.requests.get")
    def test_current_matches_fetches_and_caches_page(self, get, robots, cache_set, cache_get):
        response = Mock()
        response.status_code = 200
        response.text = self.html
        response.raise_for_status.return_value = None
        get.return_value = response
        from predict.npfl import current_matches
        result = current_matches(force_refresh=True)
        self.assertEqual(len(result), 3)
        robots.assert_called_once()
        cache_set.assert_called_once()
