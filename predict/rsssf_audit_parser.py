"""Experimental RSSSF fixture line parser, intentionally not wired to ingestion."""
import re
from datetime import date

DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?(?!\d)")
SCORE_RE = re.compile(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b")
ROUND_RE = re.compile(r"\b(?:round|matchday|week|journee| jornada)\s*(\d+)\b", re.I)


def parse_rsssf_text(text, season_year=2026):
    """Extract only reasonably clear dated home-away lines from text.

    Returns records and parser diagnostics. Ambiguous lines are skipped rather
    than guessing. This is a candidate audit parser, not production ingestion.
    """
    records, skipped = [], []
    round_name = None
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = " ".join(raw.strip().split())
        if not line:
            continue
        round_match = ROUND_RE.search(line)
        if round_match:
            round_name = round_match.group(0)
        date_match = DATE_RE.search(line)
        if not date_match:
            continue
        try:
            day, month = int(date_match.group(1)), int(date_match.group(2))
            year_text = date_match.group(3)
            year = int(year_text) if year_text else int(season_year)
            if year < 100:
                year += 2000
            match_date = date(year, month, day)
        except ValueError:
            skipped.append({"line": line_number, "reason": "invalid_date", "text": line[:240]})
            continue
        remainder = line[date_match.end():].strip(" :-|")
        # Strip simple leading round markers or fixture labels, not arbitrary content.
        remainder = re.sub(r"^(?:round|matchday|week)\s*\d+\s*[:.-]?\s*", "", remainder, flags=re.I)
        # Locate a score if present; split teams at the score, otherwise at ' - '.
        score_match = SCORE_RE.search(remainder)
        score = None
        if score_match:
            home = remainder[:score_match.start()].strip(" :-|")
            away = remainder[score_match.end():].strip(" :-|")
            score = (int(score_match.group(1)), int(score_match.group(2)))
        else:
            parts = re.split(r"\s+[-–—]\s+", remainder, maxsplit=1)
            if len(parts) != 2:
                skipped.append({"line": line_number, "reason": "unrecognized_fixture", "text": line[:240]})
                continue
            home, away = parts[0].strip(), parts[1].strip()
        # Remove trailing metadata commonly following score/team names.
        home = re.sub(r"\s{2,}.*$", "", home).strip()
        away = re.split(r"\s{2,}|\s+(?:\(|\[)", away, maxsplit=1)[0].strip()
        if not home or not away or home.casefold() == away.casefold():
            skipped.append({"line": line_number, "reason": "invalid_teams", "text": line[:240]})
            continue
        records.append({
            "date": match_date.isoformat(), "home": home, "away": away,
            "score": score, "round": round_name, "source_line": line_number,
            "status": "finished" if score is not None else "scheduled",
        })
    # Preserve first record, drop exact duplicate date/team identities.
    unique, seen = [], set()
    for record in records:
        key = (record["date"], record["home"].casefold(), record["away"].casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return {"fixtures": unique, "skipped": skipped, "lines_scanned": len(text.splitlines())}
