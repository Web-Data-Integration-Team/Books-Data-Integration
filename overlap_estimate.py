"""
Entity overlap estimate between the three book datasets (for the project abstract).

Blocking key = normalised title + surname of the first author.
Normalisation: lower-case, drop "(...)"/"[...]" (e.g. DBpedia's "Ammonite (novel)"),
drop subtitles after ":" or " - ", drop punctuation and a leading "The/A/An".

This is a rough estimate (exact key match). To support it in the abstract, the script
also writes a random sample of matched pairs to data/overlap_sample_check.csv:
check ~50 rows by hand and report the share that are true matches.

Usage:  python overlap_estimate.py
"""

import json
import re
import xml.etree.ElementTree as ET

import pandas as pd


def norm_title(t):
    if not isinstance(t, str):
        return ""
    t = t.lower()
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"\s*(:| - ).*$", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"^(the|a|an) ", "", t)


def surname(a):
    if not isinstance(a, str) or not a.strip():
        return ""
    a = re.sub(r"\(.*?\)", "", a)
    words = re.sub(r"[^a-z ]", " ", a.lower()).split()
    return words[-1] if words else ""


def key(title, first_author):
    t, s = norm_title(title), surname(first_author)
    return f"{t}|{s}" if t and s else None


# --- load ---------------------------------------------------------------
amazon = pd.read_csv("data/amazon_books_subset.csv")
goodreads = pd.read_csv("data/books_1.Best_Books_Ever.csv")
dbpedia = pd.DataFrame(
    {"title": b.findtext("title"), "authors": [a.text for a in b.findall("authors/author") if a.text]}
    for b in ET.parse("data/dbpedia_books.xml").getroot().findall("book")
)

amazon["key"] = [key(t, (json.loads(a) or [""])[0] if isinstance(a, str) else "")
                 for t, a in zip(amazon.title, amazon.authors)]
goodreads["key"] = [key(t, str(a).split(",")[0]) for t, a in zip(goodreads.title, goodreads.author)]
dbpedia["key"] = [key(t, a[0] if a else "") for t, a in zip(dbpedia.title, dbpedia.authors)]

A, G, D = (set(df.key.dropna()) for df in (amazon, goodreads, dbpedia))

# --- report -------------------------------------------------------------
print(f"Records: Amazon {len(amazon):,} | Goodreads {len(goodreads):,} | DBpedia {len(dbpedia):,}")
print(f"Amazon ∩ Goodreads:     {len(A & G):>6,}")
print(f"Amazon ∩ DBpedia:       {len(A & D):>6,}")
print(f"Goodreads ∩ DBpedia:    {len(G & D):>6,}")
print(f"In all three:           {len(A & G & D):>6,}")
print(f"In at least two:        {len((A & G) | (A & D) | (G & D)):>6,}   (requirement: >= 1,000)")
print(f"Distinct entities total:{len(A | G | D):>7,}   (requirement: >= 2,500, ideally 10k-100k)")

# --- sample for manual check ---------------------------------------------
# The checked sample is kept: if the file already exists, it is not overwritten.
SAMPLE_FILE = "data/overlap_sample_check.csv"
import os
if os.path.exists(SAMPLE_FILE):
    print(f"\n{SAMPLE_FILE} already exists (manually checked), not overwritten.")
    raise SystemExit
sample = []
for name, left, right, ks in [("Amazon-Goodreads", amazon, goodreads, A & G),
                              ("Amazon-DBpedia", amazon, dbpedia, A & D),
                              ("Goodreads-DBpedia", goodreads, dbpedia, G & D)]:
    for k in pd.Series(sorted(ks)).sample(min(20, len(ks)), random_state=42):
        l, r = left[left.key == k].iloc[0], right[right.key == k].iloc[0]
        sample.append({"pair": name, "key": k, "title_left": l.title, "title_right": r.title, "true_match (y/n)": ""})
pd.DataFrame(sample).to_csv(SAMPLE_FILE, index=False)
print("\nWrote 60 matched pairs to data/overlap_sample_check.csv for a manual precision check.")
