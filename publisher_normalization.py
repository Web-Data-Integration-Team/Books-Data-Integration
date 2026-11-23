"""Shared, conservative publisher-name normalization for both notebooks.

It folds punctuation and accents, expands common abbreviations, and removes
legal-form/generic suffixes. It retains potentially meaningful parenthetical
content and words such as ``press``, ``group``, ``sons``, and market/location
tokens (for example ``USA`` or ``UK``). This can reduce recall for variants
with extra qualifiers, but avoids treating distinct imprints or markets as the
same publisher by default.

Both notebooks use these same functions:
- Identity Resolution: ``normalize_publisher`` for its normalized string feature.
- Data Fusion: ``normalize_publisher`` for voting and ``pub_jaccard`` for
  comparisons/evaluation. Fused publisher values remain source spellings.
"""
import re
import unicodedata

PUB_STOP = {"books", "book", "publishing", "publishers", "publisher", "publications", "publication", "pr",
            "inc", "ltd", "llc", "limited", "co", "company", "corp", "corporation", "pvt", "verlag",
            "the", "and"}


def ascii_lower(s):
    """ASCII-folded, lower-cased string ('' for non-strings)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower() if isinstance(s, str) else ""


PUB_ABBR = {"pub": "publishing", "pubs": "publishing", "publ": "publishing",
            "univ": "university", "intl": "international", "natl": "national", "assn": "association"}


def strip_accents(s):
    """Lower case, accents removed ("é" -> "e"); letters of other scripts (Arabic, Cyrillic, Hangul ...) are kept."""
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", s).lower()


def pub_tokens(v):
    """Return normalized words, keeping parenthetical locations and name parts."""
    if not isinstance(v, str) or not v.strip() or v.strip().casefold() in {"nan", "none", "<na>", "nat"}:
        return set()
    s = strip_accents(v).replace("’", "'")
    # Parenthetical text can identify a market, location, imprint, or corporate
    # relationship. Unwrap it instead of deleting its contents.
    s = re.sub(r"\((.*?)\)", r" \1 ", s).replace("&", " and ")
    s = re.sub(r"'s\b", "", s).replace("'", "").replace(".", " ")   # "putnam's" -> "putnam", "int'l" -> "intl"
    s = re.sub(r"\s+", " ", re.sub(r"[^\w ]|_", " ", s)).strip()
    s = re.sub(r"\b([a-z])\s(?=[a-z]\b)", r"\1", s)             # "g p putnam" -> "gp putnam"
    toks = {PUB_ABBR.get(w, w) for w in s.split()}
    return toks - PUB_STOP


def normalize_publisher(s):
    """Normalised publisher as a string: the sorted words of pub_tokens ('' for missing values)."""
    if not isinstance(s, str) or not s.strip():
        return ""
    return " ".join(sorted(pub_tokens(s)))


def pub_jaccard(a, b):
    """Jaccard of normalized publisher words (0.0 when either side has no words)."""
    ta, tb = pub_tokens(a), pub_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def word_jaccard(x, y):
    """Word Jaccard of two normalised strings (0.0 if one is empty)."""
    a, b = set(x.split()), set(y.split())
    return len(a & b) / len(a | b) if a and b else 0.0
