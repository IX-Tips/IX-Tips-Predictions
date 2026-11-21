"""Experimental RSSSF text parser; audit-only and never wired to ingestion."""
import re
from datetime import date

NUMERIC_DATE_RE = re.compile(
    r"^\s*(\d{1,2})[./-](\d{1,2})(?:[./-]\s*(\d{2,4}))?\s*(.*)$"
)
BRACKET_DATE_RE = re.compile(
    r"^\s*\[([A-Za-z]{3,9})\s+(\d{1,2})(?:,\s*(\d{4}))?\]\s*(.*)$"
)
ROUND_RE = re.compile(
    r"^\s*(?:round|matchday|week|journee|jornada)\s*(\d+)\s*$", re.I
)
SCORE_RE = re.compile(
    r"^\s*(?P<home>.+?)\s+(?P<home_goals>\d{1,2})\s*[-:]\s*"
    r"(?P<away_goals>\d{1,2})\s+(?P<away>.+?)\s*$"
)
SCHEDULED_RE = re.compile(r"^\s*(?P<home>.+?)\s+[-–—]\s+(?P<away>.+?)\s*$")
MONTHS = {
    name.casefold(): number
    for number, names in enumerate(
        (
            ("Jan", "January"), ("Feb", "February"), ("Mar", "March"),
            ("Apr", "April"), ("May",), ("Jun", "June"),
            ("Jul", "July"), ("Aug", "August"), ("Sep", "Sept", "September"),
            ("Oct", "October"), ("Nov", "November"), ("Dec", "December"),
        ),
        1,
    )
    for name in names
}


def _make_date(day_text, month_text, year_text, season_year, season_start_month):
    day, month = int(day_text), int(month_text)
    year = int(year_text) if year_text else (
        int(season_year) + (1 if month < int(season_start_month) else 0)
    )
    if year < 100:
        year += 2000
    return date(year, month, day)


def parse_rsssf_text(text, season_year=2026, season_start_month=7):
    """Parse RSSSF round/date blocks and dated fixture rows.

    Supports numeric dates on their own line or before a fixture, and RSSSF
    month-name date headers such as [Oct 10] followed by one fixture per line.
    Undated/TBA fixtures and ambiguous rows are not guessed.
    """
    records, skipped = [], []
    round_name = None
    current_date = None

    for line_number, raw in enumerate(text.splitlines(), 1):
        line = " ".join(raw.strip().split())
        if not line:
            continue

        round_match = ROUND_RE.match(line)
        if round_match:
            round_name = f"Round {round_match.group(1)}"
            current_date = None
            continue

        if line.casefold() in {"[tba]", "[to be announced]", "tba"}:
            current_date = None
            continue

        remainder = line
        numeric_date = NUMERIC_DATE_RE.match(line)
        bracket_date = BRACKET_DATE_RE.match(line)
        try:
            if bracket_date:
                month = MONTHS.get(bracket_date.group(1).casefold())
                if month is None:
                    raise ValueError("unknown_month")
                current_date = _make_date(
                    bracket_date.group(2), month, bracket_date.group(3),
                    season_year, season_start_month,
                )
                remainder = bracket_date.group(4).strip()
            elif numeric_date:
                current_date = _make_date(
                    numeric_date.group(1), numeric_date.group(2),
                    numeric_date.group(3), season_year, season_start_month,
                )
                remainder = numeric_date.group(4).strip()
        except ValueError:
            skipped.append({
                "line": line_number, "reason": "invalid_date", "text": line[:240]
            })
            current_date = None
            continue

        if not remainder:
            continue
        if current_date is None:
            continue

        score_match = SCORE_RE.match(remainder)
        scheduled_match = None if score_match else SCHEDULED_RE.match(remainder)
        match = score_match or scheduled_match
        if not match:
            if re.search(r"\s\d{1,2}\s*[-:]\s*\d{1,2}\s", remainder):
                skipped.append({
                    "line": line_number, "reason": "ambiguous_fixture",
                    "text": line[:240],
                })
            continue

        home = match.group("home").strip()
        away = match.group("away").strip()
        if not home or not away or home.casefold() == away.casefold():
            skipped.append({
                "line": line_number, "reason": "invalid_teams", "text": line[:240]
            })
            continue

        score = (
            (int(score_match.group("home_goals")), int(score_match.group("away_goals")))
            if score_match else None
        )
        records.append({
            "date": current_date.isoformat(),
            "home": home,
            "away": away,
            "score": score,
            "round": round_name,
            "source_line": line_number,
            "status": "finished" if score is not None else "scheduled",
        })

    unique, seen = [], set()
    for record in records:
        key = (
            record["date"], record["home"].casefold(), record["away"].casefold()
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)

    return {
        "fixtures": unique,
        "skipped": skipped,
        "lines_scanned": len(text.splitlines()),
    }
