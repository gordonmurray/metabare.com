#!/usr/bin/env python3
"""Run a query against both backends and print the top-k side-by-side.

Used when shaking out demo queries for the social-media post:
which queries return visibly different ranks on /search vs
/search-mv, and which are the strongest "multi-vector wins" to
record on video.

Usage:
    python3 scripts/compare-queries.py "coffee on a messy desk"
    python3 scripts/compare-queries.py --k 5 "the chirp signal"
    python3 scripts/compare-queries.py --file scripts/demo-queries.txt
"""
import argparse
import json
import sys
from pathlib import Path

import requests

SEARCH_URL = "http://localhost:8081"


def fetch_top(endpoint: str, text: str, k: int) -> list[dict]:
    resp = requests.get(
        f"{SEARCH_URL}{endpoint}",
        params={"text": text, "k": k},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def run_query(text: str, k: int) -> None:
    print(f"\n{'=' * 70}")
    print(f"QUERY: {text}  (top {k})")
    print("=" * 70)

    try:
        single = fetch_top("/search", text, k)
    except Exception as e:
        single = [{"error": str(e)}]
    try:
        mv = fetch_top("/search-mv", text, k)
    except Exception as e:
        mv = [{"error": str(e)}]

    print(f"{'#':<3} {'SINGLE-VECTOR (CLIP)':<36} {'MULTI-VECTOR (ColPali + RRF)':<36}")
    print(f"{'-' * 3} {'-' * 35} {'-' * 35}")
    for i in range(max(len(single), len(mv))):
        s = single[i] if i < len(single) else {}
        m = mv[i] if i < len(mv) else {}
        s_label = s.get("filename", s.get("error", "—"))[:30]
        m_label = m.get("filename", m.get("error", "—"))[:30]
        s_score = s.get("score")
        m_score = m.get("score")
        s_score_str = f"{s_score:.3f}" if isinstance(s_score, (int, float)) else "—"
        m_score_str = f"{m_score:.3f}" if isinstance(m_score, (int, float)) else "—"
        print(f"{i + 1:<3} {s_label:<28} {s_score_str:>6}  {m_label:<28} {m_score_str:>6}")

    # Highlight where the top hits differ
    if single and mv and single[0].get("filename") != mv[0].get("filename"):
        print("  ↑ TOP HITS DIFFER")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*", help="Query text (or pass --file)")
    ap.add_argument("--k", type=int, default=3, help="top-k per backend (default 3)")
    ap.add_argument("--file", type=Path, help="newline-separated query file")
    args = ap.parse_args()

    if args.file:
        queries = [line.strip() for line in args.file.read_text().splitlines() if line.strip() and not line.startswith("#")]
    elif args.query:
        queries = [" ".join(args.query)]
    else:
        ap.error("supply a query argument or --file")

    for q in queries:
        run_query(q, args.k)


if __name__ == "__main__":
    main()
