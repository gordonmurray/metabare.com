#!/usr/bin/env python3
"""Ingest the curated photo + arXiv-page corpora into Metabare.

Walks the build outputs under scripts/{photo,arxiv}-corpus/data/,
joins each image with the descriptive text from the sibling
manifest.json, and POSTs it to /upload (single-vector) and/or
/upload-mv (multi-vector with text). On --build-fts the script
also POSTs /ns/{images-mv}/fts-index so the multivector namespace
gets a BM25 index for the RRF hybrid path.

Photo descriptions come from photos.json::scout_description.
arXiv page descriptions are assembled as
"{title}. {authors}. {year}." plus the highlight_pages entry when
the page is one of the figure-bearing pages, otherwise just the
paper-level header. This is what Firn's FTS column scores against
so queries like "the chirp signal" can hit LIGO page 2 even if the
multi-vector path also picks it on visual evidence alone.

Usage:
    python3 scripts/ingest-corpus.py [--mv-only|--single-only]
                                     [--reset] [--build-fts]
                                     [--photo-only|--arxiv-only]
"""
import argparse
import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PHOTO_DIR = REPO_ROOT / "scripts" / "photo-corpus" / "data"
ARXIV_DIR = REPO_ROOT / "scripts" / "arxiv-corpus" / "data"
UPLOAD_URL = "http://localhost:8080"
FIRN_URL = "http://localhost:3000"
NS_SINGLE = "images"
NS_MV = "images-mv"


def photo_rows():
    """Yield (jpg_path, description) for each curated photo."""
    manifest_path = PHOTO_DIR / "manifest.json"
    if not manifest_path.is_file():
        print(f"  skip: {manifest_path} not found (build the photo corpus first)", file=sys.stderr)
        return
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["photos"]:
        path = PHOTO_DIR / entry["filename"]
        if not path.is_file():
            print(f"  skip: {path} missing on disk", file=sys.stderr)
            continue
        desc = entry.get("scout_description") or ""
        category = entry.get("category") or ""
        text = f"{desc} ({category.replace('_', ' ')})" if category else desc
        yield path, text.strip()


def arxiv_rows():
    """Yield (jpg_path, description) for each rendered arXiv page."""
    manifest_path = ARXIV_DIR / "manifest.json"
    if not manifest_path.is_file():
        print(f"  skip: {manifest_path} not found (build the arXiv corpus first)", file=sys.stderr)
        return
    manifest = json.loads(manifest_path.read_text())
    for paper in manifest["papers"]:
        header = f"{paper['title']}. {paper['authors']} {paper['year']}."
        highlights = paper.get("highlight_pages", {}) or {}
        for page in paper["pages"]:
            page_no = page["page_no"]
            path = ARXIV_DIR / paper["arxiv_id"] / page["filename"]
            if not path.is_file():
                print(f"  skip: {path} missing on disk", file=sys.stderr)
                continue
            highlight = highlights.get(str(page_no), "")
            text = f"{header} Page {page_no}."
            if highlight:
                text = f"{text} {highlight}"
            yield path, text


def post_upload(endpoint: str, path: Path, text: str = None) -> tuple[bool, str]:
    files = {"file": (path.name, path.open("rb"), "image/jpeg")}
    data = {"text": text} if text else None
    try:
        resp = requests.post(
            f"{UPLOAD_URL}{endpoint}",
            files=files,
            data=data,
            timeout=120,
        )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, resp.json().get("filename", path.name)
    except requests.RequestException as e:
        return False, str(e)


def delete_namespace(ns: str) -> None:
    try:
        resp = requests.delete(f"{FIRN_URL}/ns/{ns}", timeout=30)
        if resp.status_code in (200, 204, 404):
            print(f"  reset: deleted namespace {ns}")
        else:
            print(f"  reset: {ns} → HTTP {resp.status_code}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"  reset: {ns} → {e}", file=sys.stderr)


def build_fts(ns: str) -> None:
    try:
        resp = requests.post(f"{FIRN_URL}/ns/{ns}/fts-index", timeout=30)
        if resp.status_code in (200, 202):
            print(f"  fts-index: {ns} → {resp.status_code}")
        else:
            print(f"  fts-index: {ns} → HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"  fts-index: {ns} → {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mv-only", action="store_true", help="skip /upload single-vector path")
    ap.add_argument("--single-only", action="store_true", help="skip /upload-mv multi-vector path")
    ap.add_argument("--photo-only", action="store_true", help="skip arXiv-page corpus")
    ap.add_argument("--arxiv-only", action="store_true", help="skip photo corpus")
    ap.add_argument("--reset", action="store_true", help="DELETE both namespaces before ingest")
    ap.add_argument("--build-fts", action="store_true", help="POST /ns/{mv}/fts-index after ingest")
    args = ap.parse_args()

    run_single = not args.mv_only
    run_mv = not args.single_only

    if args.reset:
        if run_single:
            delete_namespace(NS_SINGLE)
        if run_mv:
            delete_namespace(NS_MV)

    rows = []
    if not args.arxiv_only:
        rows.extend(photo_rows())
    if not args.photo_only:
        rows.extend(arxiv_rows())

    print(f"Found {len(rows)} image(s).")
    if not rows:
        print("Nothing to ingest. Build the corpora first (see scripts/{photo,arxiv}-corpus/README.md).", file=sys.stderr)
        sys.exit(1)

    ok_single = fail_single = ok_mv = fail_mv = 0
    for path, text in rows:
        if run_single:
            ok, msg = post_upload("/upload", path)
            tag = "ok  " if ok else "FAIL"
            print(f"{tag} single  {path.name[:32]}... → {msg[:60]}")
            if ok:
                ok_single += 1
            else:
                fail_single += 1
        if run_mv:
            ok, msg = post_upload("/upload-mv", path, text=text)
            tag = "ok  " if ok else "FAIL"
            print(f"{tag} mv      {path.name[:32]}... → {msg[:60]}")
            if ok:
                ok_mv += 1
            else:
                fail_mv += 1

    print()
    print("Summary")
    if run_single:
        print(f"  single-vector: {ok_single} ok, {fail_single} failed")
    if run_mv:
        print(f"  multi-vector:  {ok_mv} ok, {fail_mv} failed")

    if args.build_fts and run_mv and ok_mv > 0:
        print()
        print("Building FTS index on multi-vector namespace")
        build_fts(NS_MV)

    if fail_single or fail_mv:
        print(f"\nFAIL: {fail_single} single + {fail_mv} mv upload(s) failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
