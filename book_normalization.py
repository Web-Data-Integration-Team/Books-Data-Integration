"""Shared, source-preserving normalization for book titles and publication years."""

from __future__ import annotations

import re
from datetime import date


# Historical Data Fusion rules: remove one trailing parenthetical addition,
# then only the explicitly selected descriptive colon suffixes. Repeat so
# ``Title: A Novel (Series)`` is reduced consistently. Do not strip arbitrary
# colon suffixes, which may be part of a legitimate title. Consequently, some
# subtitle forms outside the selected patterns may remain; this is not complete
# subtitle extraction or a guarantee that every value is only a main title.
TRAIL_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
TRAIL_GENRE = re.compile(
    r"\s*:\s*an? (novel|thriller|memoir|mystery|romance|novella)\b.*$", re.I
)
# DBpedia may encode a secondary title as ``Main  secondary  1``.
SECONDARY = re.compile(r"^(.+?)\s{2,}.*\s{2,}\d+$")

_ISO_DATE = re.compile(r"^(\d{4})(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?(?:[T ].*)?$")
_YEAR_ONLY = re.compile(r"^\d{4}$")
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_MONTH_DATE = re.compile(rf"^(?:\w+\s+)?(?:\d{{1,2}}\s+)?({_MONTHS})\s+\d{{1,2}}?,?\s+(\d{{4}})$", re.I)
_MONTH_YEAR = re.compile(rf"^(?:{_MONTHS})\s+\d{{4}}$", re.I)
_YEAR_MONTH = re.compile(r"^(\d{1,2})/\d{1,2}/(\d{4})$")


def normalize_main_title(value, source=None):
    """Return a normalized main title, preserving values not safely reducible.

    The caller should retain the original source value separately for provenance.
    ``source='dbpedia'`` enables the DBpedia infobox secondary-title cleanup.
    """
    if not isinstance(value, str):
        return value
    result = value.strip()
    if source == "dbpedia":
        match = SECONDARY.match(result)
        if match:
            result = match.group(1).strip()
    while True:
        previous = result
        result = TRAIL_PAREN.sub("", result).strip()
        result = TRAIL_GENRE.sub("", result).strip()
        if result == previous or not result:
            return previous if not result else result


def normalize_publication_year(value):
    """Return a four-digit year for recognized date/year forms; otherwise None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if 1 <= value <= 9999 else None
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and 1 <= value <= 9999 else None
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "<na>", "nat", "null"}:
        return None
    if _YEAR_ONLY.fullmatch(text):
        year = int(text)
        return text if 1 <= year <= 9999 else None
    match = _ISO_DATE.fullmatch(text)
    if match:
        year = int(match.group(1))
        # Reject invalid calendar dates where month/day were supplied.
        parts = re.match(r"^\d{4}[-/](\d{1,2})(?:[-/](\d{1,2}))?", text)
        if parts and parts.group(1):
            month, day = int(parts.group(1)), int(parts.group(2) or 1)
            try:
                date(year, month, day)
            except ValueError:
                return None
        return match.group(1) if 1 <= year <= 9999 else None
    match = _YEAR_MONTH.fullmatch(text)
    if match:
        year, month = int(match.group(2)), int(match.group(1))
        try:
            date(year, month, 1)
        except ValueError:
            return None
        return str(year)
    if _MONTH_YEAR.fullmatch(text):
        year = int(text[-4:])
        return str(year) if 1 <= year <= 9999 else None
    match = _MONTH_DATE.fullmatch(text)
    if match:
        year = int(match.group(2))
        return str(year) if 1 <= year <= 9999 else None
    # Common US numeric date: M/D/YYYY or MM/DD/YYYY.
    match = re.fullmatch(r"\d{1,2}/\d{1,2}/(\d{4})", text)
    if match:
        year = int(match.group(1))
        return str(year) if 1 <= year <= 9999 else None
    return None
