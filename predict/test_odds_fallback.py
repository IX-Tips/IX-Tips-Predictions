from django.test import SimpleTestCase

from .utils import _normalize_apifootball_odds


class ApiFootballOddsFallbackTests(SimpleTestCase):
    def test_normalizes_match_winner_totals_and_btts_markets(self):
        payload = {
            "errors": [],
            "response": [{
                "fixture": {"id": 12345},
                "teams": {
                    "home": {"name": "Arsenal"},
                    "away": {"name": "Chelsea"},
                },
                "bookmakers": [{
                    "name": "Example Bookmaker",
                    "bets": [
                        {
                            "name": "Match Winner",
                            "values": [
                                {"value": "Home", "odd": "1.80"},
                                {"value": "Draw", "odd": "3.60"},
                                {"value": "Away", "odd": "4.50"},
                            ],
                        },
                        {
                            "name": "Goals Over/Under",
                            "values": [
                                {"value": "Over 2.5", "odd": "1.95"},
                                {"value": "Under 2.5", "odd": "1.85"},
                            ],
                        },
                        {
                            "name": "Both Teams Score",
                            "values": [
                                {"value": "Yes", "odd": "1.75"},
                                {"value": "No", "odd": "2.05"},
                            ],
                        },
                    ],
                }],
            }],
        }

        normalized = _normalize_apifootball_odds(payload)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["home_team"], "Arsenal")
        self.assertEqual(normalized[0]["away_team"], "Chelsea")
        markets = normalized[0]["bookmakers"][0]["markets"]
        self.assertEqual(markets[0]["key"], "h2h")
        self.assertEqual(
            [outcome["name"] for outcome in markets[0]["outcomes"]],
            ["Arsenal", "Draw", "Chelsea"],
        )
        self.assertEqual(markets[1]["key"], "over_under")
        self.assertEqual(markets[2]["key"], "btts")

    def test_ignores_empty_or_invalid_provider_responses(self):
        self.assertEqual(_normalize_apifootball_odds(None), [])
        self.assertEqual(_normalize_apifootball_odds({"errors": {"token": "invalid"}}), [])
        self.assertEqual(_normalize_apifootball_odds({"response": [{}]}), [])
