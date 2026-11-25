"""Shared publisher-name normalization for both notebooks.

The same publisher is written in many ways ("Riverhead" / "Riverhead Books", "Hill and Wang" / "Hill & Wang",
"G. P. Putnam's Sons" / "G.P. Putnam's Sons", "Viking" / "Viking Press", "Simon & Schuster UK" / "Simon & Schuster").
pub_tokens folds accents and punctuation, expands common abbreviations, drops parenthetical notes and removes
legal-form, generic and market words. Imprints with their own name stay different ("HarperTorch" vs "Harper").
Known false positive: "Penguin Books" == "Penguin Press".

Both notebooks use these same functions:
- Identity Resolution: ``normalize_publisher`` + ``word_jaccard`` for the publisher_norm feature.
- Data Fusion: ``pub_tokens`` for voting (publisher_vote) and the tolerant evaluation (publisher_match).
  Fused publisher values remain source spellings.
"""
import re
import unicodedata

PUB_STOP = {"books", "book", "publishing", "publishers", "publisher", "publications", "publication", "press", "pr",
            "inc", "ltd", "llc", "limited", "co", "company", "corp", "corporation", "group", "pvt", "verlag",
            "uk", "usa", "us", "the", "and", "sons"}

PUB_ABBR = {"pub": "publishing", "pubs": "publishing", "publ": "publishing",
            "univ": "university", "intl": "international", "natl": "national", "assn": "association"}

_NULLS = {"", "nan", "none", "<na>", "nat"}


def ascii_lower(s):
    """ASCII-folded, lower-cased string ('' for non-strings)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower() if isinstance(s, str) else ""


def strip_accents(s):
    """Lower case, accents removed ("é" -> "e"); letters of other scripts (Arabic, Cyrillic, Hangul ...) are kept."""
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", s).lower()


def pub_tokens(v):
    """Normalised word set of a publisher name (empty set for missing values).
    Lower case without accents, parenthetical notes removed, '&' = 'and', apostrophes dropped, initials joined,
    abbreviations expanded, generic words removed. If only generic words are left, they are kept ("The Book Group")."""
    if not isinstance(v, str) or v.strip().casefold() in _NULLS:
        return set()
    s = strip_accents(v).replace("’", "'")
    s = re.sub(r"\(.*?\)", " ", s).replace("&", " and ")         # "(Australia)", "(US)" ... are dropped
    # Apostrophes are dropped ("putnam's" -> "putnams", "int'l" -> "intl"): the data writes "St Martins" / "St. Martin's",
    # "Childrens" / "Children's" far more often than "Putnam" / "Putnam's" (6 vs 1 matched groups).
    s = s.replace("'", "").replace(".", " ")
    s = re.sub(r"\s+", " ", re.sub(r"[^\w ]|_", " ", s)).strip()
    s = re.sub(r"\b([a-z])\s(?=[a-z]\b)", r"\1", s)             # "g p putnam" -> "gp putnam"
    toks = {PUB_ABBR.get(w, w) for w in s.split()}
    return (toks - PUB_STOP) or toks


def normalize_publisher(s):
    """Normalised publisher as a string: the sorted words of pub_tokens ('' for missing values)."""
    return " ".join(sorted(pub_tokens(s)))


def pub_jaccard(a, b):
    """Jaccard of normalized publisher words (0.0 when either side has no words)."""
    ta, tb = pub_tokens(a), pub_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def word_jaccard(x, y):
    """Word Jaccard of two normalised strings (0.0 if one is empty)."""
    a, b = set(x.split()), set(y.split())
    return len(a & b) / len(a | b) if a and b else 0.0
