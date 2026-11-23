"""Shared publisher-name normalization for identity resolution and data fusion.

The same publisher is written in many ways across the sources ("Riverhead" / "Riverhead Books",
"Hill and Wang" / "Hill & Wang", "G. P. Putnam's Sons" / "G.P. Putnam's Sons").
Legal-form and generic words are removed before comparing.
Imprints stay different ("HarperTorch" vs "Harper"). Known false positive: "Penguin Books" == "Penguin Press".

Both notebooks use the same rules:
- identity_resolution.ipynb: ``normalize_publisher`` (string) + ``word_jaccard`` for the ML feature.
- data_fusion.ipynb: ``pub_tokens`` / ``pub_jaccard`` for voting and evaluation.
"""
import re
import unicodedata

PUB_STOP = {"books", "book", "publishing", "publishers", "publisher", "publications", "publication", "press", "pr",
            "inc", "ltd", "llc", "limited", "co", "company", "corp", "corporation", "group", "pvt", "verlag",
            "uk", "usa", "us", "the", "and", "sons"}


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
    """Normalised word set of a publisher name (accents removed, '&' = 'and', possessive 's removed,
    initials joined, abbreviations expanded, generic words removed)."""
    s = strip_accents(str(v)).replace("’", "'")
    s = re.sub(r"\(.*?\)", " ", s).replace("&", " and ")
    s = re.sub(r"'s\b", "", s).replace("'", "").replace(".", " ")   # "putnam's" -> "putnam", "int'l" -> "intl"
    s = re.sub(r"\s+", " ", re.sub(r"[^\w ]|_", " ", s)).strip()
    s = re.sub(r"\b([a-z])\s(?=[a-z]\b)", r"\1", s)             # "g p putnam" -> "gp putnam"
    toks = {PUB_ABBR.get(w, w) for w in s.split()}
    return (toks - PUB_STOP) or toks


def normalize_publisher(s):
    """Normalised publisher as a string: the sorted words of pub_tokens ('' for missing values)."""
    if not isinstance(s, str) or not s.strip():
        return ""
    return " ".join(sorted(pub_tokens(s)))


def pub_jaccard(a, b):
    """Jaccard of the normalised publisher word sets (0.0 if neither has a word, e.g. "-----")."""
    ta, tb = pub_tokens(a), pub_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def word_jaccard(x, y):
    """Word Jaccard of two normalised strings (0.0 if one is empty)."""
    a, b = set(x.split()), set(y.split())
    return len(a & b) / len(a | b) if a and b else 0.0
