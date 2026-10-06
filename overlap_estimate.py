"""
Entity overlap estimate between the three book datasets (for the project abstract).

Blocking key = normalised title + surname of the first author.

Normalisation:
  lower case, accents removed (e.g. Garcia Marquez), "(...)" and "[...]" dropped
  (e.g. DBpedia's "Ammonite (novel)"), optional subtitle removal after ":" or " - ",
  punctuation dropped, leading "The/A/An" and trailing ", The" dropped.
Surname: last word of the first author, ignoring suffixes such as Jr, Sr, III.

This is a rough estimate (exact key match). To support it in the abstract, the script
also writes a random sample of matched pairs to data/overlap_sample_check.csv.
Check about 60 rows by hand, fill the last column with y or n, and run the script
again. It then prints the share of true matches per dataset pair.

Usage:  python overlap_estimate.py
"""

import ast
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET

import pandas as pd

# Set to False if the manual check shows many false matches for series books
# (e.g. "Star Wars: Episode I" and "Star Wars: Episode II" both becoming "star wars").
STRIP_SUBTITLE = True

SAMPLE_FILE = "data/overlap_sample_check.csv"
SAMPLE_SIZE_PER_PAIR = 20
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}


def ascii_lower(s):
    """Lower case and strip accents: 'García Márquez' -> 'garcia marquez'."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def norm_title(t):
    if not isinstance(t, str):
        return ""
    t = ascii_lower(t)
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    if STRIP_SUBTITLE:
        t = re.sub(r"\s*(:| - ).*$", "", t)
    t = re.sub(r",\s*(the|a|an)\s*$", "", t.strip())  # "Hobbit, The" -> "Hobbit"
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"^(the|a|an) ", "", t)


def surname(a):
    if not isinstance(a, str) or not a.strip():
        return ""
    a = re.sub(r"\(.*?\)", "", ascii_lower(a))
    words = re.sub(r"[^a-z ]", " ", a).split()
    words = [w for w in words if w not in SUFFIXES]
    return words[-1] if words else ""


def key(title, first_author):
    t, s = norm_title(title), surname(first_author)
    return f"{t}|{s}" if t and s else None


def first_author_amazon(a):
    """Amazon authors can be a JSON list, a Python style list string, or a plain string."""
    if isinstance(a, (list, tuple)):
        parsed = list(a)
    elif isinstance(a, str) and a.strip():
        s = a.strip()
        parsed = None
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(s)
                break
            except (ValueError, SyntaxError):
                continue
        if parsed is None:
            parsed = [s]
    else:
        return ""
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, (list, tuple)) and parsed:
        return str(parsed[0])
    return ""


def first_author_goodreads(a):
    """Goodreads: 'Suzanne Collins, Mary GrandPre (Illustrator)' -> 'Suzanne Collins'."""
    return a.split(",")[0] if isinstance(a, str) else ""


# --- load ---------------------------------------------------------------
amazon = pd.read_csv("data/amazon_books_subset.csv")
goodreads = pd.read_csv("data/books_1.Best_Books_Ever.csv")
dbpedia = pd.DataFrame(
    {
        "title": b.findtext("title"),
        "authors": [a.text for a in b.findall("authors/author") if a.text],
    }
    for b in ET.parse("data/dbpedia_book_data.xml").getroot().findall("book")
)

amazon["key"] = [key(t, first_author_amazon(a)) for t, a in zip(amazon.title, amazon.authors)]
goodreads["key"] = [key(t, first_author_goodreads(a)) for t, a in zip(goodreads.title, goodreads.author)]
dbpedia["key"] = [key(t, a[0] if a else "") for t, a in zip(dbpedia.title, dbpedia.authors)]

A, G, D = (set(df.key.dropna()) for df in (amazon, goodreads, dbpedia))
at_least_two = (A & G) | (A & D) | (G & D)

# --- report -------------------------------------------------------------
print(f"Subtitle stripping: {STRIP_SUBTITLE}")
print(f"Records: Amazon {len(amazon):,} | Goodreads {len(goodreads):,} | DBpedia {len(dbpedia):,}")
print(
    "Records without key (excluded): "
    f"Amazon {amazon.key.isna().sum():,} | Goodreads {goodreads.key.isna().sum():,} "
    f"| DBpedia {dbpedia.key.isna().sum():,}"
)
print(f"Distinct keys: Amazon {len(A):,} | Goodreads {len(G):,} | DBpedia {len(D):,}")
print()
print(f"Amazon ∩ Goodreads:     {len(A & G):>6,}")
print(f"Amazon ∩ DBpedia:       {len(A & D):>6,}")
print(f"Goodreads ∩ DBpedia:    {len(G & D):>6,}")
print(f"In all three:           {len(A & G & D):>6,}")
print(f"In at least two:        {len(at_least_two):>6,}   (requirement: >= 1,000)")
print(f"Distinct entities total:{len(A | G | D):>7,}   (requirement: >= 2,500, ideally 10k to 100k)")

# --- manual check: evaluate if already filled, otherwise create ----------
if os.path.exists(SAMPLE_FILE):
    chk = pd.read_csv(SAMPLE_FILE)
    col = "true_match (y/n)"
    chk[col] = chk[col].astype(str).str.strip().str.lower()
    done = chk[chk[col].isin(["y", "n"])]
    print(f"\n{SAMPLE_FILE} already exists, not overwritten.")
    if done.empty:
        print("No y/n answers filled in yet.")
    else:
        print(f"Manual precision check ({len(done)} pairs rated):")
        for pair, g in done.groupby("pair"):
            print(f"  {pair}: {(g[col] == 'y').sum()}/{len(g)} true matches")
        print(f"  Overall: {(done[col] == 'y').sum()}/{len(done)} "
              f"({(done[col] == 'y').mean():.0%})")
    raise SystemExit

first = {
    "Amazon": amazon.dropna(subset=["key"]).drop_duplicates("key").set_index("key"),
    "Goodreads": goodreads.dropna(subset=["key"]).drop_duplicates("key").set_index("key"),
    "DBpedia": dbpedia.dropna(subset=["key"]).drop_duplicates("key").set_index("key"),
}

sample = []
for name, left, right, ks in [
    ("Amazon_Goodreads", "Amazon", "Goodreads", A & G),
    ("Amazon_DBpedia", "Amazon", "DBpedia", A & D),
    ("Goodreads_DBpedia", "Goodreads", "DBpedia", G & D),
]:
    if not ks:
        continue
    picks = pd.Series(sorted(ks)).sample(min(SAMPLE_SIZE_PER_PAIR, len(ks)), random_state=42)
    for k in picks:
        sample.append(
            {
                "pair": name,
                "key": k,
                "title_left": first[left].loc[k, "title"],
                "title_right": first[right].loc[k, "title"],
                "true_match (y/n)": "",
            }
        )

os.makedirs(os.path.dirname(SAMPLE_FILE), exist_ok=True)
pd.DataFrame(sample).to_csv(SAMPLE_FILE, index=False)
print(f"\nWrote {len(sample)} matched pairs to {SAMPLE_FILE} for a manual precision check.")
print("Fill the last column with y or n, then run the script again.")