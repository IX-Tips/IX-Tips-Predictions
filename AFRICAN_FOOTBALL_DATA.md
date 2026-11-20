# African football data (free source)

IX-Tips now has an optional OpenFootball provider for 14 African domestic top flights:

- Algeria — Ligue Professionnelle 1
- Egypt — Premier League
- Ghana — Premier League
- Kenya — FKF Premier League
- Malawi — Super League
- Morocco — Botola Pro
- Nigeria — Professional Football League
- Rwanda — Premier League
- Senegal — Ligue 1
- South Africa — Premiership
- Tanzania — Premier League
- Uganda — Premier League
- Zambia — Super League
- Zimbabwe — Premier Soccer League

## Source and licence

Match schedules and results are read from the [openfootball/world](https://github.com/openfootball/world) repository. Its README describes African coverage and its repository uses the CC0 1.0 Universal public-domain dedication. IX-Tips does not require an API key or paid subscription for this provider.

OpenFootball is a volunteer-maintained dataset. Updates are not guaranteed and the available season depth differs by country. The provider checks the repository index and fetches only season files that actually exist; it does not manufacture missing fixtures or scores.

## Prediction behaviour

- Finished matches with full-time scores are used for model training.
- Upcoming fixtures are eligible only when a current-season file exists and contains future fixtures.
- Match dates are retained as date-only values because the source files do not consistently provide kickoff times.
- Betting odds are not supplied by this source. The model's odds-derived features therefore use its existing missing-data handling; no odds are fabricated.
- Malawi and Zimbabwe are configured as calendar-year competitions; the other listed leagues use split seasons.

As of the initial integration (20 November 2026), the African files inspected in the source repository were generally from 2024/25 or 2025/26. This does not establish that current 2026/27 fixtures are available. The provider will automatically discover them if and when they are published.

## Attribution

OpenFootball — https://github.com/openfootball/world (CC0 1.0 Universal).
