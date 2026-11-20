# open-matchday fallback provider

IX-Tips uses the public [open-matchday dataset](https://github.com/oladeeayo/open-matchday) as an additional fallback for selected competitions. It requires no API key and is kept separate from football-data.org, football-data.co.uk, and Live-Football-Data.

## Competitions mapped

- Belgium: Belgian Pro League (BEL)
- Turkey: Süper Lig (TUR)
- Greece: Super League Greece (GRE)
- Scotland: Premiership (SCO)
- Germany: 2. Bundesliga (GER2)
- Italy: Serie B (ITA2)
- Spain: Segunda División (ESP2)
- France: Ligue 2 (FRA2)
- Argentina: Liga Profesional (ARG)
- Mexico: Liga MX (MEX)
- United States: MLS (USA)
- Japan: J1 League (JPN)
- China: Chinese Super League (CHN)

## Fallback behavior

- Upcoming fixture discovery tries the configured primary source first, then open-matchday for mapped competitions.
- A primary provider's fixture rows win when the same fixture is returned by both sources.
- Training ingestion uses open-matchday only when the primary provider returns no rows for a season. The feed currently publishes the current season only, so it does not manufacture historical seasons.
- Blank score cells remain null; they are never interpreted as 0-0.
- CSV responses are cached to reduce repeated downloads. Fixture cache expires sooner than completed-result cache.
- The source is not live. The feed is refreshed daily and its kickoff times use a fixed CET-1 convention; summer kickoff times may be off by one hour.
- Set `OPEN_MATCHDAY_ENABLED=false` to disable the integration, or set `OPEN_MATCHDAY_BASE_URL` to use a compatible mirror.

## Non-commercial use and attribution

The dataset's published terms allow personal projects, research, journalism, dashboards, and open-source tools for informational, non-commercial use with attribution. They prohibit selling the data, sublicensing it, charging for access, or presenting it as a commercial data feed. IX-Tips is currently non-commercial.

The application should display an attribution such as:

> Football data: [open-matchday](https://github.com/oladeeayo/open-matchday)

The dataset's disclaimer also notes that upstream providers retain their own rights and terms. Review the upstream terms before changing IX-Tips into a commercial product or redistributing the raw CSVs.
