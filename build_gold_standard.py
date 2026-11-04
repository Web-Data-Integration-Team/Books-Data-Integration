"""
Build the identity-resolution gold standard (Phase II) from the three translated XML files.

Entity = EDITION. Labels come from the ISBN (weak supervision), which the matchers must NOT use.
Composition per dataset pair follows the lecture (WDI05, slide 34), default 1,500 pairs:
  * 20% random matches          shared ISBN-13 (ISBN-10 converted)
  * 25% corner-case matches     shared ISBN, but the titles look different (word Jaccard < 0.5),
                                e.g. "The Last Straw" vs. "Diary of a Wimpy Kid: The Last Straw"
  * 25% corner-case non-matches same normalised title + first-author surname, no shared ISBN
                                -> same work, DIFFERENT edition
  * 30% random non-matches      random pairs of records with ISBN that share no ISBN

For each dataset pair (goodreads-amazon, goodreads-dbpedia, amazon-dbpedia) it writes
    gold_standard/<pair>_train.csv, <pair>_test.csv   columns id1,id2,label (TRUE/FALSE), 70/30 split
    gold_standard/<pair>_all_with_type.csv            all pairs with their type
    gold_standard/<pair>_check.csv                    all pairs with titles, for the manual check
Pairs found to be wrong in the manual check go into excluded_pairs.csv (id1,id2,reason) and are
dropped on the next run.

Usage (from the repo root):
    python build_gold_standard.py --goodreads data/goodreads.xml --amazon data/amazon.xml --dbpedia data/dbpedia.xml

If the XML files have no <isbn> element, the ISBNs are taken from the raw files
(data/books_1.Best_Books_Ever.csv, data/amazon_books_subset.csv, data/dbpedia_book_data.xml)
via the auto-numbered id; the script checks that the titles line up first.
    python build_gold_standard.py ... --max-per-class 500     (default 500 per class and pair)
"""

import argparse
import random
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pandas as pd

SEED = 42
STOP = {"the", "and", "for", "with", "from", "book", "books", "novel", "volume", "edition", "series"}


# --- ISBN ----------------------------------------------------------------------
def isbn13s(raw):
    """All valid ISBN-13s in a string (ISBN-10 converted); ignores placeholders like 9999999999999."""
    if not raw:
        return set()
    out = set()
    for tok in re.findall(r"(?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx]", raw):
        d = re.sub(r"[^0-9Xx]", "", tok).upper()
        if len(set(d)) == 1:
            continue
        if len(d) == 13 and d.isdigit() and d.startswith(("978", "979")):
            if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d)) % 10 == 0:
                out.add(d)
        elif len(d) == 10 and d[:9].isdigit() and \
                sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d)) % 11 == 0:
            core = "978" + d[:9]
            s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
            out.add(core + str((10 - s % 10) % 10))
    return out


# --- work key -------------------------------------------------------------------
def ascii_lower(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def norm_title(t):
    t = ascii_lower(t or "")
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)          # (Movie Tie-In), [Paperback], (novel)
    t = re.sub(r"\s*(:| - ).*$", "", t)                   # subtitles
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"^(the|a|an) ", "", t)


def surname(a):
    w = re.sub(r"[^a-z ]", " ", ascii_lower(re.sub(r"\(.*?\)", "", a or ""))).split()
    return w[-1] if w else ""


# --- load one translated file (target schema) ------------------------------------
def load(path, name, raw_isbns=None):
    """raw_isbns: optional {id: isbn text} taken from the raw file, used when the XML has no <isbn>."""
    rows = []
    for b in ET.parse(path).getroot().iter("book"):
        title = (b.findtext("title") or "").strip()
        authors = [a.text.strip() for a in b.findall("authors/author") if a.text and a.text.strip()]
        isbn_text = " ".join(e.text or "" for e in b.iter("isbn"))   # works for <isbn> or <isbns><isbn>
        if not isbn_text.strip() and raw_isbns is not None:
            isbn_text = raw_isbns.get(b.get("id"), "")
        t, s = norm_title(title), surname(authors[0] if authors else "")
        rows.append({"id": b.get("id"), "title": title, "author": authors[0] if authors else "",
                     "isbns": isbn13s(isbn_text), "work": f"{t}|{s}" if t and s else None,
                     "tokens": {w for w in norm_title(title).split()
                                if len(w) >= 3 and w.isalpha() and w not in STOP}})
    df = pd.DataFrame(rows)
    dup = df["id"].duplicated().sum()
    if df["id"].isna().any() or dup:
        raise SystemExit(f"{name}: {df['id'].isna().sum()} records without id, {dup} duplicate ids - fix the mapping first.")
    print(f"{name:<10} {len(df):>7,} records | with ISBN {df.isbns.str.len().gt(0).mean():.1%} | id example: {df.id.iloc[0]}")
    return df


# --- ISBNs from the raw files (the translated XMLs do not contain <isbn>) -----------
# MapForce numbers the records in input order, so  <prefix>_<k>  is row k of the raw file.
# The alignment is checked on the titles before the ISBNs are used.
def raw_isbns(name, raw_path, xml_titles, prefix):
    if name == "goodreads":
        raw = pd.read_csv(raw_path, dtype=str)
        titles, isbns = raw["title"].fillna(""), raw["isbn"].fillna("")
    elif name == "amazon":
        raw = pd.read_csv(raw_path, dtype=str)
        titles = raw["title"].fillna("")
        isbns = raw["isbn13"].fillna("") + " " + raw["isbn10"].fillna("")
    else:
        books = list(ET.parse(raw_path).getroot().iter("book"))
        titles = [(b.findtext("title") or "") for b in books]
        isbns = [" ".join(e.text or "" for e in b.findall("isbns/isbn")) for b in books]
    titles, isbns = list(titles), list(isbns)
    out, ok = {}, 0
    for rid, xt in xml_titles.items():
        k = int(rid[len(prefix):]) - 1
        if 0 <= k < len(titles):
            out[rid] = isbns[k]
            ok += titles[k].strip() == xt
    share = ok / max(len(xml_titles), 1)
    print(f"  {name}: ISBNs taken from {raw_path} by id; titles line up for {share:.2%}")
    if share < 0.99:
        raise SystemExit(f"{name}: ids do not line up with the raw file rows - cannot take ISBNs from it.")
    return out


def xml_titles(path):
    return {b.get("id"): (b.findtext("title") or "").strip() for b in ET.parse(path).getroot().iter("book")}


def title_sim(t1, t2):
    """Jaccard similarity of the cleaned title words (only used to find corner cases)."""
    a, b = set(norm_title(t1).split()), set(norm_title(t2).split())
    return len(a & b) / len(a | b) if a | b else 0.0


def build_pair(L, R, size, rng):
    """Gold standard following the lecture (WDI05, slide 34):
         20% random matches | 25% corner-case matches (shared ISBN, but titles look different)
         25% corner-case non-matches (same title + author, different edition) | 30% random non-matches"""
    # all matches: shared ISBN
    by_isbn = defaultdict(list)
    for rid, isb in zip(R.id, R.isbns):
        for i in isb:
            by_isbn[i].append(rid)
    pos = {(lid, rid) for lid, isb in zip(L.id, L.isbns) for i in isb for rid in by_isbn.get(i, [])}
    lt, rt = L.set_index("id")["title"].to_dict(), R.set_index("id")["title"].to_dict()
    corner_pos = {p for p in pos if title_sim(lt[p[0]], rt[p[1]]) < 0.5}

    # corner-case non-matches: same work (title + first-author surname), both with ISBN, none shared
    r_work = defaultdict(list)
    for rid, w, isb in zip(R.id, R.work, R.isbns):
        if isinstance(w, str) and isb:
            r_work[w].append((rid, isb))
    corner_neg = set()
    for lid, w, isb in zip(L.id, L.work, L.isbns):
        if isinstance(w, str) and isb:
            for rid, risb in r_work.get(w, []):
                if not (isb & risb):
                    corner_neg.add((lid, rid))

    # random non-matches: random pairs of records with ISBN that share no ISBN
    Lr = [(i, s_) for i, s_ in zip(L.id, L.isbns) if s_]
    Rr = [(i, s_) for i, s_ in zip(R.id, R.isbns) if s_]
    n_rand_neg = int(size * 0.30)
    rand_neg = set()
    while len(rand_neg) < n_rand_neg:
        (lid, li), (rid, ri) = rng.choice(Lr), rng.choice(Rr)
        if not (li & ri):
            rand_neg.add((lid, rid))

    def sample(pool, k, exclude=()):
        pool = sorted(set(pool) - set(exclude))
        return rng.sample(pool, min(k, len(pool)))

    cp = sample(corner_pos, int(size * 0.25))
    rp = sample(pos, int(size * 0.20), exclude=cp)
    cn = sample(corner_neg, int(size * 0.25))
    gs = ([(a, b, "TRUE", "random match") for a, b in rp] + [(a, b, "TRUE", "corner-case match") for a, b in cp]
          + [(a, b, "FALSE", "corner-case non-match") for a, b in cn]
          + [(a, b, "FALSE", "random non-match") for a, b in sorted(rand_neg)])
    stats = (len(pos), len(corner_pos), len(corner_neg))
    return pd.DataFrame(gs, columns=["id1", "id2", "label", "type"]), stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goodreads", required=True)
    ap.add_argument("--amazon", required=True)
    ap.add_argument("--dbpedia", required=True)
    ap.add_argument("--size", type=int, default=1500, help="gold-standard pairs per dataset pair")
    ap.add_argument("--raw-goodreads", default="data/books_1.Best_Books_Ever.csv")
    ap.add_argument("--raw-amazon", default="data/amazon_books_subset.csv")
    ap.add_argument("--raw-dbpedia", default="data/dbpedia_book_data.xml")
    ap.add_argument("--out", default="gold_standard")
    ap.add_argument("--exclude", default="excluded_pairs.csv",
                    help="manually checked wrong positives (id1,id2,reason); dropped after sampling")
    args = ap.parse_args()

    files = {"goodreads": (args.goodreads, args.raw_goodreads, "goodreads_id_"),
             "amazon": (args.amazon, args.raw_amazon, "amazon_id_"),
             "dbpedia": (args.dbpedia, args.raw_dbpedia, "dbpedia_id_")}
    data = {}
    for name, (xml_path, raw_path, prefix) in files.items():
        has_isbn = any((e.text or "").strip() for e in ET.parse(xml_path).getroot().iter("isbn"))
        lookup = None if has_isbn else raw_isbns(name, raw_path, xml_titles(xml_path), prefix)
        data[name] = load(xml_path, name.capitalize(), lookup)
    ids = [set(d.id) for d in data.values()]
    if (ids[0] & ids[1]) or (ids[0] & ids[2]) or (ids[1] & ids[2]):
        raise SystemExit("The same id appears in two datasets (e.g. DBpedia also uses 'goodreads_id_'). "
                         "Give every dataset its own prefix in the mapping first.")

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    excluded = set()
    if Path(args.exclude).exists():
        ex = pd.read_csv(args.exclude)
        excluded = set(zip(ex.id1, ex.id2))
        print(f"Excluding {len(excluded)} manually checked wrong pairs from {args.exclude}")
    rng = random.Random(SEED)
    print(f"\n{'pair':<20}{'matches':>9}{'corner m.':>11}{'corner nm.':>12}   -> gold standard (train / test)")
    for a, b in [("goodreads", "amazon"), ("goodreads", "dbpedia"), ("amazon", "dbpedia")]:
        gs, (np_, ncp, ncn) = build_pair(data[a], data[b], args.size, rng)
        gs = gs.sample(frac=1, random_state=SEED).reset_index(drop=True)
        if excluded:
            before = len(gs)
            gs = gs[[(a_, b_) not in excluded for a_, b_ in zip(gs.id1, gs.id2)]].reset_index(drop=True)
            dropped = before - len(gs)
        else:
            dropped = 0
        cut = int(len(gs) * 0.7)
        name = f"{a}_{b}"
        gs.iloc[:cut][["id1", "id2", "label"]].to_csv(out / f"{name}_train.csv", index=False)
        gs.iloc[cut:][["id1", "id2", "label"]].to_csv(out / f"{name}_test.csv", index=False)
        gs.to_csv(out / f"{name}_all_with_type.csv", index=False)
        A_, B_ = data[a].set_index("id"), data[b].set_index("id")
        chk = gs.copy()   # every pair, to verify by hand (lecture: manually verify > 500 pairs)
        chk["title1"], chk["title2"] = chk.id1.map(A_["title"]), chk.id2.map(B_["title"])
        chk["author1"], chk["author2"] = chk.id1.map(A_["author"]), chk.id2.map(B_["author"])
        # flag likely label errors: a "match" whose author surnames differ or whose titles share no word
        chk["check_carefully"] = [
            lab == "TRUE" and (surname(x) != surname(y) or title_sim(t1, t2) == 0)
            for lab, x, y, t1, t2 in zip(chk.label, chk.author1.fillna(""), chk.author2.fillna(""),
                                         chk.title1.fillna(""), chk.title2.fillna(""))]
        chk = chk.sort_values(["check_carefully", "type"], ascending=[False, True])
        chk["correct (y/n)"] = ""
        chk.to_csv(out / f"{name}_check.csv", index=False)
        print(f"{name:<20}{np_:>9,}{ncp:>11,}{ncn:>12,}   -> {cut} / {len(gs) - cut}  "
              f"({(gs.label == 'TRUE').sum()} TRUE, {(gs.label == 'FALSE').sum()} FALSE"
              f"{f', {dropped} excluded' if dropped else ''})")
    print(f"\nWritten to {out}/ . Check the *_check.csv files by hand before using the gold standard.")


if __name__ == "__main__":
    main()
