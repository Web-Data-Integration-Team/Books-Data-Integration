"""
Extract a subset of Amazon Books EDITIONS (WDI project, Phase I).

Source: McAuley-Lab / Amazon-Reviews-2023, raw/meta_categories/meta_Books.jsonl (Hugging Face)
https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023  (4,448,181 records, ~14 GB)

Entity = EDITION (one Amazon product = one edition, e.g. the 2019 Putnam hardcover of
"Where the Crawdads Sing"). Editions are NOT merged: different editions of the same work
stay separate records, which is exactly what identity resolution has to tell apart.

Selection (documented for the project proposal):
  * only records with title, at least one author and an ISBN-10 or ISBN-13
    (an ISBN identifies one edition and is our gold-standard key; Kindle/Audible
    products have no ISBN and are excluded),
  * --sample top    (default): the N records with the most Amazon ratings.
                    Rationale: Goodreads "Best Books Ever" is itself a popularity-based
                    list, so popular Amazon editions are the ones that can overlap with it.
  * --sample random: N records drawn uniformly at random (fixed seed) from the same
                    filtered population -- used as the baseline to show how much the
                    popularity selection increases the overlap.

Usage
-----
    python amazon_subset.py raw/meta_Books.jsonl                     # top 40,000 -> data/amazon_books_subset.csv
    python amazon_subset.py raw/meta_Books.jsonl --sample random     # random 40,000 -> data/amazon_books_random.csv
    python amazon_subset.py raw/meta_Books.jsonl --inspect           # look at the first raw records
    python amazon_subset.py raw/meta_Books.jsonl --count             # population sizes for the proposal text
"""

import argparse
import sys
from pathlib import Path

import duckdb

RAW_COLUMNS = {
    "parent_asin": "VARCHAR",
    "title": "VARCHAR",
    "subtitle": "VARCHAR",        # e.g. "Hardcover – Import, January 1, 2004"
    "author": "JSON",             # {"name": ..., "about": ..., "avatar": ...}
    "store": "VARCHAR",           # e.g. "Jane Doe (Author), X (Illustrator)"
    "average_rating": "DOUBLE",
    "rating_number": "BIGINT",
    "price": "VARCHAR",
    "categories": "VARCHAR[]",
    "details": "JSON",            # {"Publisher": "Scholastic; 1st edition (March 1, 2002)", "Paperback": "320 pages", ...}
}

FORMATS = ["Mass Market Paperback", "Paperback", "Hardcover", "Board book", "Spiral-bound",
           "Library Binding", "Leather Bound", "Flexibound", "Turtleback", "Audio CD", "Loose Leaf"]


def src_sql(path: str) -> str:
    cols = ", ".join(f"'{k}': '{v}'" for k, v in RAW_COLUMNS.items())
    p = path.replace("'", "''")
    return f"read_json('{p}', format='newline_delimited', columns={{{cols}}}, ignore_errors=true, maximum_object_size=50000000)"


def d(key: str) -> str:
    return f"json_extract_string(details, '$.\"{key}\"')"


def clean_sql(path: str) -> str:
    """All filtered editions (the population we sample from)."""
    fmt_from_details = " ".join(f"WHEN {d(f)} IS NOT NULL THEN '{f}'" for f in FORMATS)
    return f"""
    SELECT * FROM (
      SELECT
        parent_asin                                                      AS asin,
        trim(title)                                                      AS title,
        -- authors (list): prefer author.name, else parse "Name (Author), Other (Editor)" from store
        coalesce(
          CASE WHEN json_extract_string(author, '$.name') IS NOT NULL
               THEN [json_extract_string(author, '$.name')] END,
          nullif(list_transform(
            list_filter(string_split(store, ', '), x -> x LIKE '%(Author)%'),
            x -> trim(regexp_replace(x, '\\s*\\(.*\\)\\s*$', ''))), []),
          CASE WHEN nullif(trim(store), '') IS NOT NULL
               THEN [trim(regexp_replace(store, '\\s*\\(.*\\)\\s*$', ''))] END
        )                                                                AS authors,
        -- format (binding): from subtitle "Hardcover – ..." or from the details key ("Paperback": "320 pages")
        coalesce(nullif(regexp_extract(subtitle, '^({"|".join(FORMATS)})', 1), ''),
                 CASE {fmt_from_details} END)                            AS format,
        -- publisher / edition / date from "Scholastic; 1st edition (March 1, 2002)"
        nullif(trim(regexp_replace({d("Publisher")}, '\\s*[;(].*$', '')), '')            AS publisher,
        nullif(trim(regexp_extract({d("Publisher")}, ';\\s*([^(]*)', 1)), '')            AS edition,
        coalesce({d("Publication date")},
                 nullif(regexp_extract({d("Publisher")}, '\\(([^()]*\\d{{4}})\\)\\s*$', 1), '')) AS publication_date,
        {d("Language")}                                                  AS language,
        TRY_CAST(nullif(regexp_extract(CAST(details AS VARCHAR), '(\\d+)\\s*pages', 1), '') AS INTEGER) AS pages,
        list_filter(categories, c -> c <> 'Books')                       AS genres,
        average_rating,
        rating_number                                                    AS ratings_count,
        TRY_CAST(regexp_replace(price, '[^0-9.]', '', 'g') AS DOUBLE)    AS price,
        nullif(replace({d("ISBN 13")}, '-', ''), '')                     AS isbn13,   -- gold standard only
        nullif(replace({d("ISBN 10")}, '-', ''), '')                     AS isbn10    -- gold standard only
      FROM {src_sql(path)}
      WHERE title IS NOT NULL AND trim(title) <> ''
    )
    WHERE authors IS NOT NULL
    """


def inspect(con, path: str) -> None:
    p = path.replace("'", "''")
    rel = f"read_json('{p}', format='newline_delimited', maximum_object_size=50000000)"
    rows = con.sql(f"SELECT * FROM {rel} LIMIT 3").fetchall()
    cols = con.sql(f"DESCRIBE SELECT * FROM {rel} LIMIT 3").fetchall()
    for r in rows:
        print("-" * 60)
        for (name, *_), val in zip(cols, r):
            s = str(val)
            print(f"  {name:<16} {s[:150]}{'...' if len(s) > 150 else ''}")


def extract(con, path: str, sample: str, limit: int, seed: int, require_isbn: bool, out: Path) -> None:
    where = "WHERE isbn13 IS NOT NULL OR isbn10 IS NOT NULL" if require_isbn else ""
    order = ("ratings_count DESC NULLS LAST, asin" if sample == "top"
             else f"hash(asin || '{seed}')")          # deterministic pseudo-random order
    print(f"Reading the file and selecting {limit:,} editions ({sample}) ... a few minutes for 14 GB")
    con.sql(f"""
        CREATE OR REPLACE TABLE subset AS
        SELECT row_number() OVER () AS amazon_id, *
        FROM (SELECT * FROM ({clean_sql(path)}) {where} ORDER BY {order} LIMIT {limit})
    """)

    out.parent.mkdir(parents=True, exist_ok=True)
    as_text = "SELECT * REPLACE (to_json(authors)::VARCHAR AS authors, to_json(genres)::VARCHAR AS genres) FROM subset"
    con.sql(f"COPY ({as_text}) TO '{out.as_posix()}' (HEADER, DELIMITER ',')")

    df = con.sql(as_text).df()
    ids = ("amazon_id", "asin", "isbn13", "isbn10")
    attrs = [c for c in df.columns if c not in ids]
    missing = {c: round(100 * (df[c].astype("string").str.strip().isin(["", "[]"]) | df[c].isna()).mean(), 1)
               for c in df.columns}
    mn = con.sql("SELECT min(ratings_count), median(ratings_count) FROM subset").fetchone()

    print(f"\nWrote {len(df):,} editions -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"Ratings per edition in subset: min {mn[0]:,}, median {mn[1]:,.0f}")
    print("\n=== Missing values (%) ===")
    for c, m in sorted(missing.items(), key=lambda x: -x[1]):
        print(f"  {c:<17} {m:5.1f}%{'   (MV)' if m > 30 else ''}")
    print("\nTable 1 attribute list:")
    print("  " + ", ".join(f"{c} (MV)" if missing[c] > 30 else c for c in attrs))
    print("\nSample:")
    print(df[["title", "authors", "format", "publisher", "publication_date", "isbn13"]].head(8).to_string(max_colwidth=32))


def count(con, path: str) -> None:
    n = con.sql(f"""
        SELECT count(*),
               count(*) FILTER (WHERE isbn13 IS NOT NULL OR isbn10 IS NOT NULL)
        FROM ({clean_sql(path)})
    """).fetchone()
    total = con.sql(f"SELECT count(*) FROM {src_sql(path)}").fetchone()[0]
    print(f"Raw records:                         {total:,}")
    print(f"With title and author:               {n[0]:,}")
    print(f"... and an ISBN (sampling population): {n[1]:,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="path or https URL of meta_Books.jsonl")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--sample", choices=["top", "random"], default="top")
    ap.add_argument("--limit", type=int, default=40_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--allow-no-isbn", action="store_true", help="also keep editions without ISBN")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if not args.input.startswith("http") and not Path(args.input).exists():
        sys.exit(f"File not found: {args.input}")
    out = args.out or Path("data/amazon_books_subset.csv" if args.sample == "top" else "data/amazon_books_random.csv")

    con = duckdb.connect()
    con.sql("SET memory_limit='4GB'")
    con.sql("SET preserve_insertion_order=false")
    if args.input.startswith("http"):
        con.sql("INSTALL httpfs; LOAD httpfs;")

    if args.inspect:
        inspect(con, args.input)
    elif args.count:
        count(con, args.input)
    else:
        extract(con, args.input, args.sample, args.limit, args.seed, not args.allow_no_isbn, out)


if __name__ == "__main__":
    main()
