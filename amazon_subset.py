"""
Extract a small, metadata-only subset of Amazon Books (WDI project, Phase I).

Source: McAuley-Lab / Amazon-Reviews-2023, raw/meta_categories/meta_Books.jsonl (Hugging Face)
https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023

The raw file is ~13 GB and must NOT go to GitHub (limit 100 MB per file).
This script reads it with DuckDB (streams through the file, low RAM), keeps only
the attributes we need, takes the most-rated books (most likely to overlap with
Goodreads "Best Books Ever" and DBpedia), writes a small CSV to data/, and prints
the numbers for Table 1 of the abstract.

Usage
-----
    pip install duckdb pandas

    # optional: look at the structure first (reads only the first records)
    python amazon_subset.py raw/meta_Books.jsonl --inspect

    # extract the subset (default: 40,000 most-rated books)
    python amazon_subset.py raw/meta_Books.jsonl
    python amazon_subset.py raw/meta_Books.jsonl --limit 30000

    # no disk space for 13 GB? read straight from Hugging Face instead (slower, needs stable internet)
    python amazon_subset.py https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/raw/meta_categories/meta_Books.jsonl
"""

import argparse
import sys
from pathlib import Path

import duckdb

# Only these top-level fields are read; everything else in the file (images, videos,
# description, features, ...) is skipped, which keeps it fast. Missing fields become NULL.
RAW_COLUMNS = {
    "parent_asin": "VARCHAR",
    "title": "VARCHAR",
    "subtitle": "VARCHAR",
    "author": "JSON",          # {"name": ..., "about": ..., "avatar": ...} when present
    "store": "VARCHAR",        # for books often "Jane Doe (Author)" / "Jane Doe (Author), X (Illustrator)"
    "average_rating": "DOUBLE",
    "rating_number": "BIGINT",
    "price": "VARCHAR",
    "categories": "VARCHAR[]",
    "details": "JSON",         # {"Publisher": "...; 1st edition (March 1, 2002)", "Language": ..., "Paperback": "320 pages", "ISBN 13": ...}
}


def src_sql(path: str) -> str:
    cols = ", ".join(f"'{k}': '{v}'" for k, v in RAW_COLUMNS.items())
    p = path.replace("'", "''")
    return f"read_json('{p}', format='newline_delimited', columns={{{cols}}}, ignore_errors=true, maximum_object_size=50000000)"


def d(key: str) -> str:
    """Extract a string field from the 'details' JSON."""
    return f"json_extract_string(details, '$.\"{key}\"')"


# Normalised title used only for de-duplicating editions within Amazon:
# lower-case, drop "(...)" and "[...]", drop everything after ":" or " - ",
# drop edition words, drop punctuation, drop leading/trailing articles
# ("The Girl on the Train" == "GIRL ON THE TRAIN,THE").
NORM_TITLE = r"""regexp_replace(trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
    lower(title),
    '\([^)]*\)|\[[^\]]*\]', ' ', 'g'),
    '\s*(:| - ).*$', ''),
    '\b(a novel|deluxe|anniversary|special|collectors?|movie tie-in|tie-in|paperback|hardcover|hardback|mass market|large print|kindle|audiobook|audio cd|unabridged|edition)\b', ' ', 'g'),
    '[^a-z0-9 ]|\s+', ' ', 'g')),
    '^(the|a|an) |\s+(the|a|an)$', '', 'g')"""


EXTRACT_SQL = """
WITH raw AS (SELECT * FROM {src}),
clean AS (
  SELECT
    parent_asin                                                      AS asin,
    trim(title)                                                      AS title,
    nullif(trim(subtitle), '')                                       AS subtitle,
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
    -- publisher: "Scholastic; Reprint edition (March 1, 2002)" -> "Scholastic"
    nullif(trim(regexp_replace({pub}, '\\s*[;(].*$', '')), '')       AS publisher,
    -- publication date: dedicated field, else the "(March 1, 2002)" part of Publisher
    coalesce({pubdate}, nullif(regexp_extract({pub}, '\\(([^()]*\\d{{4}})\\)\\s*$', 1), '')) AS publication_date,
    {lang}                                                           AS language,
    -- page count: first "<n> pages" found anywhere in details (Paperback, Hardcover, Print length, Spiral-bound, ...)
    TRY_CAST(nullif(regexp_extract(CAST(details AS VARCHAR), '(\\d+)\\s*pages', 1), '') AS INTEGER) AS pages,
    list_filter(categories, c -> c <> 'Books')                       AS genres,
    average_rating,
    rating_number                                                    AS ratings_count,
    TRY_CAST(regexp_replace(price, '[^0-9.]', '', 'g') AS DOUBLE)    AS price,
    replace({isbn13}, '-', '')                                       AS isbn13,   -- for the gold standard only
    {isbn10}                                                         AS isbn10    -- for the gold standard only
  FROM raw
  WHERE title IS NOT NULL AND trim(title) <> ''
    AND coalesce(rating_number, 0) >= {min_ratings}
)
SELECT row_number() OVER (ORDER BY ratings_count DESC NULLS LAST) AS amazon_id, *
FROM clean
WHERE authors IS NOT NULL
-- one record per WORK: editions like "X", "X: A Novel", "X (Movie Tie-In)", "X Deluxe Edition"
-- share a normalised title + first author. Amazon shares ratings across editions, so we keep
-- the MOST COMPLETE edition (print editions have pages/ISBN; audiobooks and Kindle often don't)
QUALIFY row_number() OVER (PARTITION BY {norm_title}, lower(trim(authors[1]))
                           ORDER BY (pages IS NOT NULL)::INT + (publisher IS NOT NULL)::INT
                                    + (publication_date IS NOT NULL)::INT + (language IS NOT NULL)::INT
                                    + (isbn13 IS NOT NULL)::INT DESC,
                                    ratings_count DESC NULLS LAST) = 1
ORDER BY ratings_count DESC NULLS LAST
LIMIT {limit}
"""


def inspect(con, path: str) -> None:
    p = path.replace("'", "''")
    print("\n=== First 3 records (raw) ===")
    rows = con.sql(f"SELECT * FROM read_json('{p}', format='newline_delimited', maximum_object_size=50000000) LIMIT 3").fetchall()
    cols = con.sql(f"DESCRIBE SELECT * FROM read_json('{p}', format='newline_delimited', maximum_object_size=50000000) LIMIT 3").fetchall()
    for r in rows:
        print("-" * 60)
        for (name, *_), val in zip(cols, r):
            s = str(val)
            print(f"  {name:<16} {s[:150]}{'...' if len(s) > 150 else ''}")


def extract(con, path: str, limit: int, min_ratings: int, out: Path) -> None:
    sql = EXTRACT_SQL.format(
        src=src_sql(path), limit=limit, min_ratings=min_ratings,
        pub=d("Publisher"), pubdate=d("Publication date"), lang=d("Language"),
        norm_title=NORM_TITLE,
        isbn13=d("ISBN 13"), isbn10=d("ISBN 10"),
    )
    print("Reading the file (a few minutes for the full 14 GB)...")
    con.sql(f"CREATE OR REPLACE TABLE subset AS {sql}")

    out.parent.mkdir(parents=True, exist_ok=True)
    # lists are written as JSON arrays ["A", "B"], same style as the Goodreads CSV,
    # so the team notebook (overlook_datasets.ipynb) recognises them as list attributes
    con.sql(f"""
        COPY (SELECT * REPLACE (to_json(authors)::VARCHAR AS authors, to_json(genres)::VARCHAR AS genres) FROM subset)
        TO '{out.as_posix()}' (HEADER, DELIMITER ',')
    """)

    df = con.sql("SELECT * REPLACE (to_json(authors)::VARCHAR AS authors, to_json(genres)::VARCHAR AS genres) FROM subset").df()
    attrs = [c for c in df.columns if c not in ("amazon_id", "asin", "isbn13", "isbn10")]
    missing = {}
    for c in df.columns:
        s = df[c].astype("string").str.strip()
        missing[c] = round(100 * (s.isna() | (s == "") | (s == "[]")).mean(), 1)

    size_mb = out.stat().st_size / 1e6
    print(f"\nWrote {len(df):,} books -> {out} ({size_mb:.1f} MB)")
    print("\n=== Missing values (%) — mark >30% as (MV) in Table 1 ===")
    for c, m in sorted(missing.items(), key=lambda x: -x[1]):
        print(f"  {c:<17} {m:5.1f}%{'   (MV)' if m > 30 else ''}")
    mv = [f"{c} (MV)" if missing[c] > 30 else c for c in attrs]
    print("\nTable 1 row (ISBN/ASIN kept only as identifiers for the gold standard, not counted as attributes):")
    print(f"  Amazon | Amazon Reviews 2023 (McAuley Lab, Hugging Face), meta_Books.jsonl, top {len(df):,} by #ratings via DuckDB"
          f" | csv | {len(df):,} | {len(attrs)} | {', '.join(mv)}")
    print("\nSample:")
    print(df[["title", "authors", "publisher", "publication_date", "pages", "ratings_count"]].head(5).to_string(max_colwidth=35))
    if size_mb > 50:
        print("\nWarning: >50 MB. Use a smaller --limit before committing to GitHub.")


def count(con, path: str) -> None:
    n = con.sql(f"SELECT count(*), count(*) FILTER (WHERE rating_number > 0) FROM {src_sql(path)}").fetchone()
    print(f"Total records in raw file: {n[0]:,}  (with at least one rating: {n[1]:,})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="path or https URL of meta_Books.jsonl")
    ap.add_argument("--inspect", action="store_true", help="print the first records to see the structure")
    ap.add_argument("--count", action="store_true", help="count all records in the raw file (for the abstract text)")
    ap.add_argument("--limit", type=int, default=40_000, help="max books in subset (default 40000)")
    ap.add_argument("--min-ratings", type=int, default=1, help="drop books with fewer ratings (default 1)")
    ap.add_argument("--out", type=Path, default=Path("data/amazon_books_subset.csv"))
    args = ap.parse_args()

    if not args.input.startswith("http") and not Path(args.input).exists():
        sys.exit(f"File not found: {args.input}")

    con = duckdb.connect()
    con.sql("SET memory_limit='4GB'")       # DuckDB spills to disk if needed
    con.sql("SET preserve_insertion_order=false")
    if args.input.startswith("http"):
        con.sql("INSTALL httpfs; LOAD httpfs;")

    if args.inspect:
        inspect(con, args.input)
    elif args.count:
        count(con, args.input)
    else:
        extract(con, args.input, args.limit, args.min_ratings, args.out)


if __name__ == "__main__":
    main()
