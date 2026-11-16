"""
Cluster cleanup across the three dataset pairs (identity resolution, Phase II).

Each pair is matched separately with one-to-one matching. Combining the three pairs can still
create clusters that contain two records of the same source (e.g. goodreads A - amazon X -
goodreads B). Since every source lists an edition at most once, such clusters must contain at
least one wrong correspondence.

Fix: go through all correspondences of all pairs from highest to lowest score and keep a
correspondence only if it does not put two records of the same source into one cluster.
The weakest conflicting correspondences are dropped; all other clusters stay unchanged.
Note: scores come from different models per pair, so they are only roughly comparable.

Usage
  from the notebook:  correspondences = enforce_one_per_source(correspondences)
  standalone:         python cluster_cleanup.py
                      (cleans data/correspondences/*.csv in place, reports test scores
                       before/after, writes data/output/identity_resolution/cluster_cleanup.csv)
"""

from pathlib import Path

import pandas as pd

PAIRS = ["goodreads_amazon", "goodreads_dbpedia", "amazon_dbpedia"]
CORR_DIR = Path("data/correspondences")
GOLD_DIR = Path("gold_standard")
OUT_DIR = Path("data/output/identity_resolution")


def source(record_id):
    return record_id.split("_id_")[0]


def enforce_one_per_source(correspondences):
    """correspondences: dict pair -> DataFrame(id1, id2, score, ...). Returns a cleaned dict."""
    edges = pd.concat([c.assign(_pair=p) for p, c in correspondences.items()], ignore_index=True)
    edges = edges.sort_values("score", ascending=False, kind="mergesort")

    parent, sources = {}, {}

    def find(x):
        if x not in parent:
            parent[x], sources[x] = x, {source(x)}
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keep = []
    for u, v in zip(edges.id1, edges.id2):
        ru, rv = find(u), find(v)
        if ru == rv:
            keep.append(True)
        elif sources[ru] & sources[rv]:
            keep.append(False)
        else:
            parent[rv] = ru
            sources[ru] |= sources[rv]
            keep.append(True)

    kept = edges[keep]
    return {p: kept[kept._pair == p].drop(columns="_pair").reset_index(drop=True) for p in correspondences}


def test_scores(corr, pair):
    gold = pd.read_csv(GOLD_DIR / f"{pair}_test.csv")
    gold["label"] = gold.label.astype(str).str.upper().eq("TRUE")
    pred = set(zip(corr.id1, corr.id2))
    hit = [(a, b) in pred for a, b in zip(gold.id1, gold.id2)]
    tp = sum(h and l for h, l in zip(hit, gold.label))
    p = tp / max(sum(hit), 1)
    r = tp / max(gold.label.sum(), 1)
    return p, r, 2 * p * r / max(p + r, 1e-9)


def main():
    raw_dir = CORR_DIR / "before_cluster_cleanup"
    raw_dir.mkdir(exist_ok=True)
    before = {}
    for p in PAIRS:
        raw = raw_dir / f"{p}.csv"
        if not raw.exists():  # first run: keep the uncleaned files
            pd.read_csv(CORR_DIR / f"{p}.csv").to_csv(raw, index=False)
        before[p] = pd.read_csv(raw)

    after = enforce_one_per_source(before)

    rows = []
    for p in PAIRS:
        pb, rb, fb = test_scores(before[p], p)
        pa, ra, fa = test_scores(after[p], p)
        rows.append({"pair": p, "correspondences before": len(before[p]), "correspondences after": len(after[p]),
                     "removed": len(before[p]) - len(after[p]),
                     "test P before": pb, "test R before": rb, "test F1 before": fb,
                     "test P after": pa, "test R after": ra, "test F1 after": fa})
        after[p].to_csv(CORR_DIR / f"{p}.csv", index=False)

    report = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report.to_csv(OUT_DIR / "cluster_cleanup.csv", index=False)
    print(report.round(3).to_string(index=False))
    print(f"\nCleaned files written to {CORR_DIR}/ (originals kept in {raw_dir}/).")


if __name__ == "__main__":
    main()
