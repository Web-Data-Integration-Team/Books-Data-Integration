"""
Overlap estimate between Goodreads, DBpedia and Amazon at EDITION level (project proposal).

Entity = edition. Two records describe the same edition if they share an ISBN
(ISBN-10 and ISBN-13 normalised to ISBN-13). ISBN is only used here and for the
gold standard -- the matchers in Phase II will not see it.

The script reports:
  1. Edition overlap (shared ISBN-13) for each pair, all three, and in >= 2 datasets.
  2. Work overlap (normalised title + first-author surname), to show how many shared
     works appear in a DIFFERENT edition in the other source. Those are the hard
     corner cases for identity resolution: same title and author, different edition.
  3. If data/amazon_books_random.csv exists (python amazon_subset.py ... --sample random):
     the overlap of a RANDOM Amazon sample with Goodreads, compared to the popularity-
     based subset (answers "why does your Amazon subset overlap so much?").

Usage:  python overlap_estimate.py
"""

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

GOODREADS = "data/books_1.Best_Books_Ever.csv"
DBPEDIA = "data/dbpedia_book_data.xml"
AMAZON = "data/amazon_books_subset.csv"
AMAZON_RANDOM = "data/amazon_books_random.csv"


# --- ISBN ------------------------------------------------------------------
def isbn13(raw):
    """All valid ISBN-13s found in a string (handles '0-14-143951-3 (pbk)', 'ISBN 978...')."""
    if not isinstance(raw, str):
        return set()
    out = set()
    for tok in re.findall(r"(?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx]", raw):
        d = re.sub(r"[^0-9Xx]", "", tok).upper()
        if len(set(d)) == 1:  # placeholders like 9999999999999
            continue
        if len(d) == 13 and d.isdigit() and d.startswith(("978", "979")) and d != "9999999999999":
            if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d)) % 10 == 0:
                out.add(d)
        elif len(d) == 10 and d[:9].isdigit() and sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d)) % 11 == 0:
            core = "978" + d[:9]
            s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
            out.add(core + str((10 - s % 10) % 10))
    return out


# --- work key (title + first-author surname) ----------------------------------
def norm_title(t):
    if not isinstance(t, str):
        return ""
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"\s*(:| - ).*$", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"^(the|a|an) ", "", t)


def surname(a):
    if not isinstance(a, str):
        return ""
    a = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", "", a)).encode("ascii", "ignore").decode()
    w = re.sub(r"[^a-z ]", " ", a.lower()).split()
    return w[-1] if w else ""


def work_key(title, author):
    t, s = norm_title(title), surname(author)
    return f"{t}|{s}" if t and s else None


# --- load --------------------------------------------------------------------
def load_goodreads():
    df = pd.read_csv(GOODREADS, dtype=str)
    if df["isbn"].str.contains(r"E\+", na=False).mean() > 0.05:
        raise SystemExit(f"{GOODREADS} has ISBNs in scientific notation (file was saved with Excel). "
                         "Replace it with the original file first.")
    df["isbns"] = df["isbn"].map(isbn13)
    df["work"] = [work_key(t, str(a).split(",")[0]) for t, a in zip(df.title, df.author)]
    return df


def load_dbpedia():
    rows = []
    for b in ET.parse(DBPEDIA).getroot().iter("book"):
        isb = set()
        for i in b.findall("isbns/isbn"):
            isb |= isbn13(i.text)
        authors = [a.text for a in b.findall("authors/author") if a.text]
        rows.append({"title": b.findtext("title"), "isbns": isb,
                     "work": work_key(b.findtext("title"), authors[0] if authors else "")})
    return pd.DataFrame(rows)


def load_amazon(path):
    df = pd.read_csv(path, dtype=str)
    df["isbns"] = [isbn13(f"{a} {b}") for a, b in zip(df.isbn13, df.isbn10)]
    first = [(json.loads(a) or [""])[0] if isinstance(a, str) and a.startswith("[") else "" for a in df.authors]
    df["work"] = [work_key(t, a) for t, a in zip(df.title, first)]
    return df


def isbn_set(df):
    return set().union(*df["isbns"]) if len(df) else set()


def work_set(df):
    return set(df["work"].dropna())


def main():
    gr, db, am = load_goodreads(), load_dbpedia(), load_amazon(AMAZON)
    G, D, A = isbn_set(gr), isbn_set(db), isbn_set(am)
    WG, WD, WA = work_set(gr), work_set(db), work_set(am)

    print(f"Records: Goodreads {len(gr):,} | DBpedia {len(db):,} | Amazon {len(am):,}")
    print(f"Records with a valid ISBN: Goodreads {(gr.isbns.str.len() > 0).mean():.1%} | "
          f"DBpedia {(db.isbns.str.len() > 0).mean():.1%} | Amazon {(am.isbns.str.len() > 0).mean():.1%}")

    print("\n1) EDITION overlap (shared ISBN-13) -- lower bound, records without ISBN cannot be counted")
    two = (A & G) | (A & D) | (G & D)
    for name, n in [("Amazon ∩ Goodreads", len(A & G)), ("Amazon ∩ DBpedia", len(A & D)),
                    ("Goodreads ∩ DBpedia", len(G & D)), ("in all three", len(A & G & D)),
                    ("in at least two  (requirement >= 1,000)", len(two))]:
        print(f"   {name:<42}{n:>7,}")

    print("\n2) WORK overlap (normalised title + first-author surname) and edition corner cases")
    for name, L, R, WL, WR in [("Amazon-Goodreads", am, gr, WA, WG), ("Amazon-DBpedia", am, db, WA, WD),
                               ("Goodreads-DBpedia", gr, db, WG, WD)]:
        shared = WL & WR
        li = L.dropna(subset=["work"]).groupby("work")["isbns"].agg(lambda s: set().union(*s))
        ri = R.dropna(subset=["work"]).groupby("work")["isbns"].agg(lambda s: set().union(*s))
        both_isbn = [w for w in shared if li.get(w) and ri.get(w)]
        diff = sum(1 for w in both_isbn if not (li[w] & ri[w]))
        print(f"   {name:<20} shared works {len(shared):>6,} | with ISBN on both sides {len(both_isbn):>6,} "
              f"| of these in a DIFFERENT edition: {diff:>6,} ({diff / max(len(both_isbn), 1):.0%})")
    multi = am.dropna(subset=["work"]).groupby("work").size()
    print(f"   Amazon works with more than one edition in the subset: {(multi > 1).sum():,} "
          f"({multi[multi > 1].sum():,} records)")

    if Path(AMAZON_RANDOM).exists():
        rnd = load_amazon(AMAZON_RANDOM)
        R_, WR_ = isbn_set(rnd), work_set(rnd)
        print("\n3) Amazon selection: popularity subset vs. random sample of the same size")
        print(f"   {'':<28}{'editions ∩ Goodreads':>22}{'works ∩ Goodreads':>20}")
        print(f"   {'top by #ratings':<28}{len(A & G):>22,}{len(WA & WG):>20,}")
        print(f"   {'random (seed 42)':<28}{len(R_ & G):>22,}{len(WR_ & WG):>20,}")


if __name__ == "__main__":
    main()
