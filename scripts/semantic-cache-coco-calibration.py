#!/usr/bin/env python3
"""COCO caption calibration benchmark for Firn semantic caching.

Run inside a Docker image with torch, transformers, Pillow, and requests:

    docker run --rm --network host -v "$PWD:/workspace" -w /workspace \
      metabare-semantic-bench \
      python scripts/semantic-cache-coco-calibration.py \
        --firn-url http://127.0.0.1:3000 \
        --out docs/semantic-cache-coco-calibration.md
"""

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import requests
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

REPO_ROOT = Path(__file__).resolve().parent.parent
COCO_ANNOTATIONS_URL = "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"
COCO_IMAGE_URL = "http://images.cocodataset.org/val2017/{file_name}"
DEBUG_CACHE_HEADER = {"x-firn-debug-cache-source": "true"}


def stable_u64(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


def download(url: str, dest: Path, timeout: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    tmp.replace(dest)


def ensure_captions(cache_dir: Path, timeout: float) -> Path:
    annotations_dir = cache_dir / "annotations"
    captions = annotations_dir / "captions_val2017.json"
    if captions.is_file():
        return captions

    zip_path = cache_dir / "annotations_trainval2017.zip"
    if not zip_path.is_file():
        print(f"downloading COCO annotations to {zip_path}")
        download(COCO_ANNOTATIONS_URL, zip_path, timeout)

    print(f"extracting {captions.name}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("annotations/captions_val2017.json", cache_dir)
    return captions


def load_coco_pairs(
    captions_path: Path,
    cache_dir: Path,
    image_count: int,
    pair_count: int,
    seed: int,
    timeout: float,
) -> Tuple[List[Dict], List[Dict]]:
    data = json.loads(captions_path.read_text())
    images_by_id = {int(img["id"]): img for img in data["images"]}
    captions_by_image: Dict[int, List[str]] = defaultdict(list)
    for ann in data["annotations"]:
        caption = " ".join(ann["caption"].strip().split())
        if caption:
            captions_by_image[int(ann["image_id"])].append(caption)

    eligible = [
        image_id
        for image_id, captions in captions_by_image.items()
        if image_id in images_by_id and len(captions) >= 2
    ]
    rng = random.Random(seed)
    rng.shuffle(eligible)
    selected_ids = eligible[:image_count]

    image_dir = cache_dir / "val2017"
    corpus = []
    for image_id in selected_ids:
        image = images_by_id[image_id]
        file_name = image["file_name"]
        path = image_dir / file_name
        if not path.is_file():
            print(f"downloading COCO image {file_name}")
            download(COCO_IMAGE_URL.format(file_name=file_name), path, timeout)
        corpus.append(
            {
                "id": image_id,
                "file_name": file_name,
                "path": str(path),
                "captions": captions_by_image[image_id],
            }
        )

    pairs = []
    for row in corpus:
        captions = row["captions"]
        pairs.append(
            {
                "image_id": row["id"],
                "file_name": row["file_name"],
                "query_a": captions[0],
                "query_b": captions[1],
            }
        )
    rng.shuffle(pairs)
    return corpus, pairs[: min(pair_count, len(pairs))]


class ClipEmbedder:
    def __init__(self, device: str, batch_size: int) -> None:
        self.device = device
        self.batch_size = batch_size
        self.model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(device)
        self.processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        self.model.eval()

    def image_vectors(self, rows: List[Dict]) -> Dict[int, np.ndarray]:
        out: Dict[int, np.ndarray] = {}
        for start in range(0, len(rows), self.batch_size):
            batch = rows[start : start + self.batch_size]
            images = []
            for row in batch:
                with Image.open(row["path"]) as image:
                    images.append(image.convert("RGB").copy())
            inputs = self.processor(images=images, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            with torch.no_grad():
                vecs = self.model.get_image_features(**inputs)
            vecs = vecs / vecs.norm(dim=1, keepdim=True)
            for row, vec in zip(batch, vecs):
                out[int(row["id"])] = vec.cpu().numpy().astype("float32")
            print(f"embedded images {min(start + self.batch_size, len(rows))}/{len(rows)}")
        return out

    def text_vectors(self, texts: List[str]) -> Dict[str, np.ndarray]:
        out: Dict[str, np.ndarray] = {}
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            inputs = self.processor(text=batch, return_tensors="pt", padding=True, truncation=True)
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            with torch.no_grad():
                vecs = self.model.get_text_features(**inputs)
            vecs = vecs / vecs.norm(dim=1, keepdim=True)
            for text, vec in zip(batch, vecs):
                out[text] = vec.cpu().numpy().astype("float32")
            print(f"embedded texts {min(start + self.batch_size, len(texts))}/{len(texts)}")
        return out


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def delete_namespace(firn_url: str, ns: str, timeout: float) -> None:
    resp = requests.delete(f"{firn_url}/ns/{ns}", timeout=timeout)
    if resp.status_code not in (200, 204, 404):
        resp.raise_for_status()


def post_json(url: str, payload: Dict, timeout: float, debug_header: bool = False) -> Tuple[Dict, str]:
    headers = DEBUG_CACHE_HEADER if debug_header else None
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    source = resp.headers.get("x-firn-cache-source", "")
    return (resp.json() if resp.content else {}), source


def upsert_rows(firn_url: str, ns: str, rows: List[Dict], timeout: float, batch_size: int) -> None:
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        post_json(f"{firn_url}/ns/{ns}/upsert", {"rows": batch}, timeout)


def query_firn(
    firn_url: str,
    ns: str,
    vector: np.ndarray,
    k: int,
    timeout: float,
    semantic_threshold: float = None,
) -> Tuple[float, Dict, str]:
    payload = {"vector": vector.astype("float32").tolist(), "k": k}
    if semantic_threshold is not None:
        payload["semantic_cache"] = {
            "enabled": True,
            "min_similarity": semantic_threshold,
        }
    start = time.perf_counter()
    body, source = post_json(
        f"{firn_url}/ns/{ns}/query",
        payload,
        timeout,
        debug_header=True,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return elapsed_ms, body, source


def result_ids(body: Dict) -> List[int]:
    return [int(row["id"]) for row in body.get("results", [])]


def overlap(a: Iterable[int], b: Iterable[int]) -> int:
    aset = set(a)
    return sum(1 for item in b if item in aset)


def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, math.floor((len(ordered) - 1) * q))
    return ordered[idx]


def mean(values: List[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def threshold_slug(threshold: float) -> str:
    return f"{threshold:.6f}".rstrip("0").rstrip(".").replace(".", "-")


def parse_thresholds(raw: str) -> List[float]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(float(item))
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firn-url", default="http://127.0.0.1:3000")
    parser.add_argument("--namespace-prefix", default="coco-semantic-calib")
    parser.add_argument("--cache-dir", default="scripts/coco-cache")
    parser.add_argument("--image-count", type=int, default=200)
    parser.add_argument("--pair-count", type=int, default=80)
    parser.add_argument("--thresholds", default="0.995,0.99,0.98,0.95,0.90,0.85,0.80,0.75")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=58)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--upsert-batch-size", type=int, default=32)
    parser.add_argument("--out", default="docs/semantic-cache-coco-calibration.md")
    parser.add_argument("--csv-out", default="docs/semantic-cache-coco-calibration.csv")
    parser.add_argument("--svg-out", default="docs/semantic-cache-coco-threshold-curve.svg")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    thresholds = parse_thresholds(args.thresholds)
    cache_dir = REPO_ROOT / args.cache_dir
    captions = ensure_captions(cache_dir, args.timeout)
    corpus, pairs = load_coco_pairs(
        captions,
        cache_dir,
        args.image_count,
        args.pair_count,
        args.seed,
        args.timeout,
    )
    if not corpus or not pairs:
        raise SystemExit("no COCO images or caption pairs available")

    embedder = ClipEmbedder(args.device, args.batch_size)
    image_vectors = embedder.image_vectors(corpus)
    texts = sorted({p["query_a"] for p in pairs} | {p["query_b"] for p in pairs})
    text_vectors = embedder.text_vectors(texts)

    upsert_payload = []
    for row in corpus:
        row_id = int(row["id"])
        upsert_payload.append(
            {
                "id": row_id,
                "vector": image_vectors[row_id].tolist(),
                "text": row["captions"][0],
            }
        )

    all_rows = []
    for threshold in thresholds:
        ns = f"{args.namespace_prefix}-{threshold_slug(threshold)}"
        print(f"resetting namespace {ns}")
        delete_namespace(args.firn_url, ns, args.timeout)
        upsert_rows(args.firn_url, ns, upsert_payload, args.timeout, args.upsert_batch_size)

        for idx, pair in enumerate(pairs):
            vec_a = text_vectors[pair["query_a"]]
            vec_b = text_vectors[pair["query_b"]]
            sim = cosine(vec_a, vec_b)

            seed_ms, seed_body, seed_source = query_firn(
                args.firn_url, ns, vec_a, args.k, args.timeout, semantic_threshold=1.0
            )
            exact_ms, _exact_body, exact_source = query_firn(
                args.firn_url, ns, vec_a, args.k, args.timeout, semantic_threshold=1.0
            )
            sem_ms, sem_body, sem_source = query_firn(
                args.firn_url, ns, vec_b, args.k, args.timeout, semantic_threshold=threshold
            )
            sem_ids = result_ids(sem_body)

            if sem_source == "semantic_cache":
                true_ms, true_body, true_source = query_firn(
                    args.firn_url, ns, vec_b, args.k, args.timeout
                )
                true_ids = result_ids(true_body)
            else:
                true_ms, true_source = sem_ms, sem_source
                true_ids = sem_ids

            target_id = int(pair["image_id"])
            all_rows.append(
                {
                    "threshold": threshold,
                    "namespace": ns,
                    "pair_index": idx,
                    "image_id": target_id,
                    "file_name": pair["file_name"],
                    "query_a": pair["query_a"],
                    "query_b": pair["query_b"],
                    "caption_cosine": sim,
                    "seed_source": seed_source,
                    "exact_source": exact_source,
                    "b_source": sem_source,
                    "true_source": true_source,
                    "seed_ms": seed_ms,
                    "exact_ms": exact_ms,
                    "b_ms": sem_ms,
                    "true_ms": true_ms,
                    "overlap": overlap(sem_ids, true_ids),
                    "target_in_reused": target_id in sem_ids,
                    "target_in_true": target_id in true_ids,
                    "reused_ids": sem_ids,
                    "true_ids": true_ids,
                }
            )
        print(f"finished threshold {threshold:.6f}")

    out_path = REPO_ROOT / args.out
    csv_path = REPO_ROOT / args.csv_out
    svg_path = REPO_ROOT / args.svg_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(args, corpus, pairs, thresholds, all_rows), encoding="utf-8")
    write_csv(csv_path, all_rows)
    svg_path.write_text(render_svg(summarize(all_rows, args.k)), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {svg_path}")


def summarize(rows: List[Dict], k: int) -> List[Dict]:
    out = []
    by_threshold: Dict[float, List[Dict]] = defaultdict(list)
    for row in rows:
        by_threshold[row["threshold"]].append(row)

    for threshold in sorted(by_threshold.keys(), reverse=True):
        group = by_threshold[threshold]
        sources = Counter(row["b_source"] or "missing" for row in group)
        hits = [row for row in group if row["b_source"] == "semantic_cache"]
        misses = [row for row in group if row["b_source"] == "backend"]
        exact = [row for row in group if row["b_source"] == "exact_cache"]
        hit_overlaps = [row["overlap"] / k for row in hits]
        out.append(
            {
                "threshold": threshold,
                "pairs": len(group),
                "semantic_hits": len(hits),
                "backend": len(misses),
                "exact": len(exact),
                "hit_rate": len(hits) / len(group) if group else 0.0,
                "mean_hit_overlap": mean(hit_overlaps),
                "p10_hit_overlap": pct(hit_overlaps, 0.10),
                "mean_all_overlap": mean([row["overlap"] / k for row in group]),
                "target_retained": mean([1.0 if row["target_in_reused"] else 0.0 for row in hits]),
                "b_p50_ms": pct([row["b_ms"] for row in group], 0.50),
                "semantic_p50_ms": pct([row["b_ms"] for row in hits], 0.50),
                "backend_p50_ms": pct([row["b_ms"] for row in misses], 0.50),
                "source_counts": sources,
            }
        )
    return out


def render_markdown(args, corpus: List[Dict], pairs: List[Dict], thresholds: List[float], rows: List[Dict]) -> str:
    summary = summarize(rows, args.k)
    caption_cosines = [row["caption_cosine"] for row in rows if row["threshold"] == thresholds[0]]
    seed_sources = Counter(row["seed_source"] or "missing" for row in rows)
    exact_sources = Counter(row["exact_source"] or "missing" for row in rows)
    b_sources = Counter(row["b_source"] or "missing" for row in rows)

    def count_line(counter: Counter) -> str:
        return ", ".join(f"`{key}` {value}" for key, value in sorted(counter.items()))

    lines = [
        "# COCO semantic cache calibration",
        "",
        f"- **Firn URL**: `{args.firn_url}`",
        f"- **Namespace prefix**: `{args.namespace_prefix}`",
        f"- **Corpus**: {len(corpus)} COCO val2017 images",
        f"- **Caption pairs**: {len(pairs)}",
        f"- **CLIP model**: `openai/clip-vit-base-patch32`",
        f"- **Device**: `{args.device}`",
        f"- **k**: {args.k}",
        f"- **Seed**: {args.seed}",
        "",
        "## Caption Cosines",
        "",
        f"- p10: {pct(caption_cosines, 0.10):.6f}",
        f"- p50: {pct(caption_cosines, 0.50):.6f}",
        f"- p90: {pct(caption_cosines, 0.90):.6f}",
        "",
        "## Threshold Curve",
        "",
        "| threshold | semantic hit rate | semantic hits | backend | mean hit overlap | p10 hit overlap | target retained on hits | B p50 | semantic p50 | backend p50 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary:
        lines.append(
            "| {threshold:.3f} | {hit_rate:.1%} | {semantic_hits}/{pairs} | {backend} | {mean_hit_overlap:.1%} | {p10_hit_overlap:.1%} | {target_retained:.1%} | {b_p50_ms:.2f} ms | {semantic_p50_ms:.2f} ms | {backend_p50_ms:.2f} ms |".format(
                **row
            )
        )

    lines.extend(
        [
            "",
            "## Source Checks",
            "",
            "- Each caption A seed query is sent with semantic caching enabled at threshold 1.0. That seeds the semantic sidecar while avoiding lower-threshold reuse during the seed step.",
            "- Each caption A repeat should report `exact_cache` through the debug header.",
            "- Each caption B query reports `backend`, `exact_cache`, or `semantic_cache` through the opt-in `x-firn-cache-source` response header.",
            "- Top-k overlap is measured against a follow-up uncached caption B query when caption B was served from `semantic_cache`.",
            f"- Observed seed sources: {count_line(seed_sources)}.",
            f"- Observed exact-repeat sources: {count_line(exact_sources)}.",
            f"- Observed caption B sources: {count_line(b_sources)}.",
            "",
            "## Worst Semantic Hits",
            "",
        ]
    )
    hits = [row for row in rows if row["b_source"] == "semantic_cache"]
    for row in sorted(hits, key=lambda item: (item["overlap"], -item["threshold"]))[:10]:
        lines.extend(
            [
                f"### threshold {row['threshold']:.3f}, overlap {row['overlap']}/{args.k}",
                "",
                f"- image: `{row['file_name']}`",
                f"- cosine: {row['caption_cosine']:.6f}",
                f"- query A: {row['query_a']}",
                f"- query B: {row['query_b']}",
                f"- target retained in reused result: {row['target_in_reused']}",
                f"- reused ids: `{row['reused_ids']}`",
                f"- true ids: `{row['true_ids']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Notes",
            "",
            "- Timings include the Firn HTTP request and result decode, but not CLIP encoding.",
            "- The benchmark uses one namespace per threshold and seeds each caption pair before probing the paired caption.",
            "- COCO images and annotations are cached under `scripts/coco-cache/`, which is ignored by git.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: List[Dict]) -> None:
    fields = [
        "threshold",
        "pair_index",
        "image_id",
        "file_name",
        "caption_cosine",
        "seed_source",
        "exact_source",
        "b_source",
        "true_source",
        "seed_ms",
        "exact_ms",
        "b_ms",
        "true_ms",
        "overlap",
        "target_in_reused",
        "target_in_true",
        "query_a",
        "query_b",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})


def render_svg(summary: List[Dict]) -> str:
    width = 760
    height = 420
    pad = 56
    points = sorted(
        (row["threshold"], row["hit_rate"], row["mean_hit_overlap"])
        for row in summary
    )
    min_threshold = min(t for t, _, _ in points)
    max_threshold = max(t for t, _, _ in points)

    def x(threshold: float) -> float:
        span = max_threshold - min_threshold
        return pad + ((threshold - min_threshold) / span) * (width - 2 * pad)

    def y(value: float) -> float:
        return height - pad - value * (height - 2 * pad)

    def polyline(index: int, color: str) -> str:
        coords = " ".join(f"{x(t):.1f},{y(row[index]):.1f}" for row in points for t in [row[0]])
        return f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{coords}"/>'

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#222"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="#222"/>',
        '<text x="56" y="28" font-size="18" font-weight="700">COCO semantic cache threshold curve</text>',
        '<text x="560" y="28" font-size="13" fill="#2563eb">hit rate</text>',
        '<text x="560" y="48" font-size="13" fill="#dc2626">mean overlap on hits</text>',
        '<text x="330" y="405" font-size="13">min_similarity threshold</text>',
    ]
    for value in [0.0, 0.25, 0.5, 0.75, 1.0]:
        lines.append(
            f'<text x="18" y="{y(value) + 5:.1f}" font-size="12">{value:.0%}</text>'
            f'<line x1="{pad}" y1="{y(value):.1f}" x2="{width - pad}" y2="{y(value):.1f}" stroke="#ddd"/>'
        )
    for threshold, _, _ in points:
        lines.append(
            f'<text x="{x(threshold) - 16:.1f}" y="{height - pad + 22}" font-size="12">{threshold:.3f}</text>'
        )
    lines.append(polyline(1, "#2563eb"))
    lines.append(polyline(2, "#dc2626"))
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
