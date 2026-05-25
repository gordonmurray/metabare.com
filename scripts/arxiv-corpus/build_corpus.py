#!/usr/bin/env python3
"""Build the arXiv page-image corpus for the multi-vector demo.

Reads papers.json, downloads each PDF from arxiv.org, renders the
listed pages to JPEG at 150 DPI, names each output <sha256>.jpg to
mirror Metabare's existing id scheme. Idempotent: skips downloads
and renders whose output already exists.

Outputs land under /work/data/<arxiv_id>/page-<NN>-<sha8>.jpg
(symlink-friendly name for human inspection; the SHA-256 of the
JPEG bytes is also recorded in the manifest produced at the end).
"""

import hashlib
import json
import sys
from pathlib import Path

import requests
from pdf2image import convert_from_path

ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
DPI = 150
JPEG_QUALITY = 85
DATA_ROOT = Path("/work/data")
PAPERS_JSON = Path("/work/papers.json")
MANIFEST_OUT = Path("/work/data/manifest.json")


def fetch_pdf(arxiv_id: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  pdf cached: {dest.name}")
        return
    url = ARXIV_PDF_URL.format(arxiv_id=arxiv_id)
    print(f"  downloading {url}")
    resp = requests.get(url, timeout=60, headers={"User-Agent": "metabare-corpus/0.1"})
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    print(f"  saved {dest.name} ({len(resp.content) // 1024} KB)")


def render_page(pdf_path: Path, page_no: int, out_dir: Path) -> dict:
    """Render a single 1-indexed page to JPEG. Return manifest row."""
    images = convert_from_path(
        str(pdf_path),
        dpi=DPI,
        first_page=page_no,
        last_page=page_no,
        fmt="jpeg",
    )
    if not images:
        raise RuntimeError(f"pdf2image returned no image for {pdf_path.name} page {page_no}")

    img = images[0]
    tmp = out_dir / f"page-{page_no:02d}.tmp.jpg"
    img.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
    raw = tmp.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    final = out_dir / f"page-{page_no:02d}-{sha[:8]}.jpg"
    tmp.rename(final)
    print(f"    page {page_no:02d} -> {final.name} ({len(raw) // 1024} KB)")
    return {
        "page_no": page_no,
        "sha256": sha,
        "filename": final.name,
        "bytes": len(raw),
    }


def main() -> int:
    config = json.loads(PAPERS_JSON.read_text())
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    manifest = {"papers": []}
    for paper in config["papers"]:
        arxiv_id = paper["arxiv_id"]
        print(f"\n== {arxiv_id}  {paper['title']}")
        paper_dir = DATA_ROOT / arxiv_id
        paper_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = paper_dir / f"{arxiv_id}.pdf"
        fetch_pdf(arxiv_id, pdf_path)

        pages = []
        for page_no in paper["pages"]:
            existing = list(paper_dir.glob(f"page-{page_no:02d}-*.jpg"))
            if existing:
                f = existing[0]
                raw = f.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                print(f"    page {page_no:02d} cached: {f.name}")
                pages.append(
                    {
                        "page_no": page_no,
                        "sha256": sha,
                        "filename": f.name,
                        "bytes": len(raw),
                    }
                )
                continue
            pages.append(render_page(pdf_path, page_no, paper_dir))

        manifest["papers"].append(
            {
                "arxiv_id": arxiv_id,
                "title": paper["title"],
                "authors": paper["authors"],
                "year": paper["year"],
                "licence": paper["licence"],
                "highlight_pages": paper.get("highlight_pages", {}),
                "pages": pages,
            }
        )

    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote {MANIFEST_OUT} ({sum(len(p['pages']) for p in manifest['papers'])} pages total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
