"""
Download ALL books (dbo:Book) from the public DBpedia endpoint -> data/dbpedia_book_data.xml

Why this replaces data/sparqlcode_dbpedia_books.py (feedback from Dr. Peeters):
  * The old query required dbo:isbn, had LIMIT 2000 per first letter and used one big
    GROUP BY with many OPTIONALs. The endpoint silently cuts off queries that run too
    long, so only 20,981 of ~59,000 books came back and counts varied between runs.
  * Year and language came from dbo: properties, which are mostly empty.

How this script avoids that:
  1. Books are split into small partitions by the first character of their URI
     (A, B, ..., Z, 0-9, other). Every partition is fetched in pages of 10,000 rows
     (LIMIT 10000 OFFSET ...). The endpoint refuses OFFSET beyond 40,000, so the
     script stops with an error if a partition would ever need that.
  2. Instead of one heavy query, every attribute is fetched with its own small,
     fast query (book -> value), and the results are joined in Python.
  3. Year and language use the raw infobox properties dbp:releaseDate, dbp:pubDate
     and dbp:language in addition to the dbo: ones, as suggested in the feedback.
  4. At the end the number of downloaded books is compared with COUNT(dbo:Book).

Usage (needs internet, takes ~10-20 minutes):
    pip install requests
    python dbpedia_books.py
"""

import re
import string
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from xml.dom import minidom

import requests

ENDPOINT = "https://dbpedia.org/sparql"
OUT = Path("data/dbpedia_book_data.xml")
PAGE = 10_000
MAX_OFFSET = 40_000
RES = "http://dbpedia.org/resource/"

PREFIXES = """
PREFIX dbo:  <http://dbpedia.org/ontology/>
PREFIX dbp:  <http://dbpedia.org/property/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

# Partitions by first character of the resource name (keeps every partition well below 40,000).
PARTITIONS = [(c, f'STRSTARTS(STR(?book), "{RES}{c}")') for c in string.ascii_uppercase + string.digits]
PARTITIONS.append(("other", f'!REGEX(STR(?book), "^{RES}[A-Z0-9]")'))

# attribute -> list of predicates. Values can be literals or resources; for resources the
# English rdfs:label is used. Order = priority when a single value is needed.
ATTRIBUTES = {
    "title":     ["rdfs:label"],
    "authors":   ["dbo:author", "dbp:author"],
    "publisher": ["dbo:publisher", "dbp:publisher"],
    "date":      ["dbo:releaseDate", "dbp:releaseDate", "dbp:pubDate", "dbo:publicationDate", "dbp:published"],
    "pages":     ["dbo:numberOfPages", "dbp:pages"],
    "language":  ["dbo:language", "dbp:language"],
    "genres":    ["dbo:literaryGenre", "dbp:genre"],
    "isbns":     ["dbo:isbn", "dbp:isbn"],
}
LIST_ATTRS = {"authors", "genres", "isbns"}

session = requests.Session()
session.headers["User-Agent"] = "UniMannheim-WDI-student-project (books)"


def sparql(query: str, tries: int = 6) -> list[dict]:
    for attempt in range(1, tries + 1):
        try:
            r = session.post(ENDPOINT, data={"query": PREFIXES + query,
                                             "format": "application/sparql-results+json"}, timeout=120)
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except Exception as e:  # timeouts, 5xx, rate limits
            wait = 5 * attempt
            print(f"    request failed ({type(e).__name__}: {str(e)[:80]}), retry {attempt}/{tries} in {wait}s")
            time.sleep(wait)
    sys.exit("Endpoint keeps failing - try again later.")


def paged(query_body: str, label: str) -> list[dict]:
    """Run a SELECT in pages of 10,000 rows with a stable ORDER BY."""
    rows, offset = [], 0
    while True:
        if offset >= MAX_OFFSET:
            sys.exit(f"Partition {label} needs OFFSET >= {MAX_OFFSET}; split it further.")
        page = sparql(f"{query_body}\nORDER BY ?book ?v\nLIMIT {PAGE} OFFSET {offset}")
        rows += page
        if len(page) < PAGE:
            return rows
        offset += PAGE
        time.sleep(0.5)


def value_of(b: dict) -> str | None:
    """English label for resources, literal value otherwise."""
    if "label" in b:
        return b["label"]["value"]
    v = b["v"]
    if v["type"] == "uri":
        if v["value"].startswith(RES):  # resource without English label -> readable name from URI
            return v["value"][len(RES):].replace("_", " ")
        return None
    return v["value"]


def main():
    total = int(sparql("SELECT (COUNT(DISTINCT ?book) AS ?n) WHERE { ?book a dbo:Book }")[0]["n"]["value"])
    print(f"dbo:Book resources on the endpoint: {total:,}\n")

    data = defaultdict(lambda: defaultdict(list))   # book -> attribute -> values (in priority order)
    books = set()

    for part, filt in PARTITIONS:
        ids = paged(f"SELECT DISTINCT ?book (1 AS ?v) WHERE {{ ?book a dbo:Book . FILTER({filt}) }}", part)
        books.update(b["book"]["value"] for b in ids)
        n_part = len(ids)
        for attr, preds in ATTRIBUTES.items():
            for pred in preds:
                lang = 'FILTER(lang(?v) = "en")' if pred == "rdfs:label" else ""
                body = f"""SELECT ?book ?v ?label WHERE {{
                    ?book a dbo:Book ; {pred} ?v . FILTER({filt}) {lang}
                    OPTIONAL {{ ?v rdfs:label ?label . FILTER(lang(?label) = "en") }}
                }}"""
                for b in paged(body, f"{part}/{pred}"):
                    val = value_of(b)
                    if val and val.strip():
                        lst = data[b["book"]["value"]][attr]
                        if val.strip() not in lst:
                            lst.append(val.strip())
                time.sleep(0.3)
        print(f"  partition {part:>5}: {n_part:>6,} books (total so far {len(books):,})")

    print(f"\nDownloaded {len(books):,} of {total:,} books")
    if len(books) < total:
        print("WARNING: fewer books than COUNT - re-run, the endpoint may have cut off a query.")

    write_xml(books, data)


def year_of(dates: list[str]) -> str:
    for d in dates:
        m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", d)
        if m:
            return m.group(1)
    return ""


def pages_of(vals: list[str]) -> str:
    for v in vals:
        m = re.search(r"\d+", v.replace(",", ""))
        if m and 0 < int(m.group()) < 20000:
            return m.group()
    return ""


def clean_language(v: str) -> str:
    return re.sub(r"\s+language$", "", v, flags=re.I).strip()


def write_xml(books, data):
    root = ET.Element("books")
    for uri in sorted(books):
        d = data.get(uri, {})
        b = ET.SubElement(root, "book")
        ET.SubElement(b, "uri").text = uri
        ET.SubElement(b, "title").text = (d.get("title") or [uri[len(RES):].replace("_", " ")])[0]
        ET.SubElement(b, "publisher").text = (d.get("publisher") or [""])[0]
        ET.SubElement(b, "release_date").text = (d.get("date") or [""])[0]   # raw value, for reference
        ET.SubElement(b, "year").text = year_of(d.get("date", []))
        ET.SubElement(b, "pages").text = pages_of(d.get("pages", []))
        ET.SubElement(b, "language").text = clean_language((d.get("language") or [""])[0])
        for attr, child in [("isbns", "isbn"), ("authors", "author"), ("genres", "genre")]:
            el = ET.SubElement(b, attr)
            for v in d.get(attr, []):
                ET.SubElement(el, child).text = v

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8"))

    # profile for Table 1
    n = len(books)
    print(f"\nWrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)\n\nMissing values (mark >30% as MV):")
    for attr in ["title", "authors", "publisher", "year", "pages", "language", "genres", "isbns"]:
        filled = sum(1 for e in root.iter("book") if (e.find(attr).text or "").strip() or len(e.find(attr)))
        miss = 100 * (1 - filled / n)
        print(f"  {attr:<10} {miss:5.1f}%{'  (MV)' if miss > 30 else ''}")


if __name__ == "__main__":
    main()
