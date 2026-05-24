#!/usr/bin/env python3
"""Fetch the curated photo corpus from Unsplash + Pexels.

Reads photos.json, downloads each photo from its source via the
public download endpoint, normalises to max-edge 1920 px JPEG at
quality 88, names the output by SHA-256 of the normalised bytes
(mirroring Metabare's <sha>.jpg id scheme), and writes a
manifest.json with full attribution.

Idempotent: skips a photo if a file matching its corpus_id is
already present in data/. Re-runs are cheap.
"""

import hashlib
import io
import json
import sys
from pathlib import Path

import requests
from PIL import Image

UNSPLASH_DOWNLOAD = "https://unsplash.com/photos/{source_id}/download"
PEXELS_DOWNLOAD = "https://images.pexels.com/photos/{source_id}/pexels-photo-{source_id}.jpeg?cs=srgb&fm=jpg"
USER_AGENT = "metabare-corpus/0.1 (+https://metabare.com)"
MAX_EDGE = 1920
JPEG_QUALITY = 88

DATA_ROOT = Path("/work/data")
PHOTOS_JSON = Path("/work/photos.json")
MANIFEST_OUT = Path("/work/data/manifest.json")
CORPUS_INDEX = Path("/work/data/by-corpus-id.json")


def build_download_url(photo: dict) -> str:
    source = photo["source"]
    source_id = photo["source_id"]
    if source == "unsplash":
        return UNSPLASH_DOWNLOAD.format(source_id=source_id)
    if source == "pexels":
        return PEXELS_DOWNLOAD.format(source_id=source_id)
    raise ValueError(f"Unknown source {source!r} for corpus_id {photo['corpus_id']}")


def fetch_bytes(url: str) -> bytes:
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
        allow_redirects=True,
        timeout=60,
    )
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    if not ctype.startswith("image/"):
        raise RuntimeError(f"Expected image/* from {url}, got {ctype!r}")
    return resp.content


def normalise_jpeg(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buf.getvalue()


def main() -> int:
    config = json.loads(PHOTOS_JSON.read_text())
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    if CORPUS_INDEX.exists():
        index = json.loads(CORPUS_INDEX.read_text())
    else:
        index = {}

    manifest = {"photos": []}
    for photo in config["photos"]:
        corpus_id = photo["corpus_id"]
        existing_sha = index.get(corpus_id)
        cached = DATA_ROOT / f"{existing_sha}.jpg" if existing_sha else None
        if cached and cached.exists():
            raw = cached.read_bytes()
            print(f"  cached {corpus_id} -> {cached.name} ({len(raw) // 1024} KB)")
            manifest["photos"].append(
                {
                    **photo,
                    "sha256": existing_sha,
                    "filename": cached.name,
                    "bytes": len(raw),
                }
            )
            continue

        url = build_download_url(photo)
        print(f"  fetching {corpus_id} {photo['source']}:{photo['source_id']}")
        raw = fetch_bytes(url)
        normalised = normalise_jpeg(raw)
        sha = hashlib.sha256(normalised).hexdigest()
        out = DATA_ROOT / f"{sha}.jpg"
        out.write_bytes(normalised)
        index[corpus_id] = sha
        print(
            f"    {corpus_id} -> {out.name} "
            f"({len(raw) // 1024} KB raw -> {len(normalised) // 1024} KB normalised)"
        )
        manifest["photos"].append(
            {
                **photo,
                "sha256": sha,
                "filename": out.name,
                "bytes": len(normalised),
            }
        )

    CORPUS_INDEX.write_text(json.dumps(index, indent=2) + "\n")
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote {MANIFEST_OUT} ({len(manifest['photos'])} photos total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
