#!/usr/bin/env python3
"""Curated-corpus benchmark for Firn semantic caching with CLIP text vectors.

Run inside a Docker image that has torch, transformers, Pillow, and
requests installed, for example:

    docker run --rm --network host -v "$PWD:/workspace" -w /workspace \
      metabare-semantic-bench \
      python scripts/semantic-cache-curated-benchmark.py \
        --out docs/semantic-cache-curated-benchmark.md
"""

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import requests
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

REPO_ROOT = Path(__file__).resolve().parent.parent
PHOTO_DIR = REPO_ROOT / "scripts" / "photo-corpus" / "data"
ARXIV_DIR = REPO_ROOT / "scripts" / "arxiv-corpus" / "data"

QUERY_PAIRS = [
    ("messy desk", "cluttered desk with papers"),
    ("laptop on a messy desk", "desk with a laptop and scattered notes"),
    ("open shop sign", "sign that says come in we are open"),
    ("paris coffee shop sign", "coffee shop sign in paris"),
    ("woman with a tattoo", "tattooed woman"),
    ("encoder decoder block diagram", "transformer architecture diagram"),
    ("image patches going into a transformer", "vision transformer patch diagram"),
    ("gravitational wave chirp signal", "ligo chirp plot"),
]


def stable_u64(name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


def load_corpus() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    photo_manifest = PHOTO_DIR / "manifest.json"
    if photo_manifest.is_file():
        manifest = json.loads(photo_manifest.read_text())
        for entry in manifest.get("photos", []):
            filename = entry["filename"]
            path = PHOTO_DIR / filename
            if not path.is_file():
                continue
            desc = entry.get("scout_description") or ""
            category = (entry.get("category") or "").replace("_", " ")
            label = f"{desc} ({category})" if category else desc
            rows.append({"filename": filename, "path": str(path), "label": label.strip()})

    arxiv_manifest = ARXIV_DIR / "manifest.json"
    if arxiv_manifest.is_file():
        manifest = json.loads(arxiv_manifest.read_text())
        for paper in manifest.get("papers", []):
            header = f"{paper['title']}. {paper['authors']} {paper['year']}."
            highlights = paper.get("highlight_pages", {}) or {}
            for page in paper.get("pages", []):
                page_no = page["page_no"]
                filename = page["filename"]
                path = ARXIV_DIR / paper["arxiv_id"] / filename
                if not path.is_file():
                    continue
                text = f"{header} Page {page_no}."
                highlight = highlights.get(str(page_no), "")
                if highlight:
                    text = f"{text} {highlight}"
                rows.append({
                    "filename": f"{paper['arxiv_id']}/{filename}",
                    "path": str(path),
                    "label": text,
                })

    return rows


class ClipEmbedder:
    def __init__(self, device: str) -> None:
        self.device = device
        self.model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(device)
        self.processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        self.model.eval()

    def image_vector(self, path: str) -> np.ndarray:
        image = Image.open(path).convert("RGB")
        inputs = self.processor(images=image, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            vec = self.model.get_image_features(**inputs).squeeze()
        vec = vec / vec.norm()
        return vec.cpu().numpy().astype("float32")

    def text_vector(self, text: str) -> np.ndarray:
        inputs = self.processor(text=[text], return_tensors="pt", padding=True, truncation=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            vec = self.model.get_text_features(**inputs).squeeze()
        vec = vec / vec.norm()
        return vec.cpu().numpy().astype("float32")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def post_json(url: str, payload: Dict, timeout: float) -> Dict:
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def delete_namespace(firn_url: str, ns: str, timeout: float) -> None:
    resp = requests.delete(f"{firn_url}/ns/{ns}", timeout=timeout)
    if resp.status_code not in (200, 204, 404):
        resp.raise_for_status()


def upsert_rows(firn_url: str, ns: str, rows: List[Dict], timeout: float) -> None:
    for start in range(0, len(rows), 16):
        batch = rows[start : start + 16]
        post_json(f"{firn_url}/ns/{ns}/upsert", {"rows": batch}, timeout)


def query_firn(
    firn_url: str,
    ns: str,
    vector: np.ndarray,
    k: int,
    timeout: float,
    semantic_threshold: float = None,
) -> Tuple[float, Dict]:
    payload = {"vector": vector.astype("float32").tolist(), "k": k}
    if semantic_threshold is not None:
        payload["semantic_cache"] = {
            "enabled": True,
            "min_similarity": semantic_threshold,
        }
    start = time.perf_counter()
    body = post_json(f"{firn_url}/ns/{ns}/query", payload, timeout)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return elapsed_ms, body


def metrics_for_namespace(firn_url: str, ns: str, timeout: float) -> Dict[str, int]:
    resp = requests.get(f"{firn_url}/metrics", timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    label = f'namespace="{ns}"'
    values = {
        "semantic_hits": 0,
        "semantic_misses": 0,
        "empty_index": 0,
        "exact_hits": 0,
        "backend_queries": 0,
    }
    mapping = {
        "firnflow_semantic_cache_hits_total": "semantic_hits",
        "firnflow_semantic_cache_misses_total": "semantic_misses",
        "firnflow_cache_hits_total": "exact_hits",
    }
    for line in text.splitlines():
        if line.startswith("#") or label not in line:
            continue
        for prefix, key in mapping.items():
            if line.startswith(prefix):
                values[key] = int(float(line.rsplit(None, 1)[1]))
        if line.startswith("firnflow_semantic_cache_rejections_total") and 'reason="empty_index"' in line:
            values["empty_index"] = int(float(line.rsplit(None, 1)[1]))
        if line.startswith("firnflow_s3_requests_total") and 'operation="query"' in line:
            values["backend_queries"] = int(float(line.rsplit(None, 1)[1]))
    return values


def ids(body: Dict) -> List[int]:
    return [int(row["id"]) for row in body.get("results", [])]


def overlap(a: Iterable[int], b: Iterable[int]) -> int:
    aset = set(a)
    return sum(1 for x in b if x in aset)


def labels_for(ids_: Iterable[int], labels: Dict[int, str]) -> List[str]:
    out = []
    for row_id in ids_:
        text = labels.get(row_id, "")
        out.append(text[:90].replace("\n", " "))
    return out


def thresholds_for(similarity: float) -> List[float]:
    values = [0.995, 0.99, max(0.01, similarity - 0.005), max(0.01, similarity - 0.02)]
    unique = []
    for value in values:
        rounded = round(value, 6)
        if rounded not in unique:
            unique.append(rounded)
    return unique


def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, math.floor((len(ordered) - 1) * q))
    return ordered[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firn-url", default="http://127.0.0.1:3000")
    parser.add_argument("--namespace-prefix", default="images-curated-semantic")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", default="docs/semantic-cache-curated-benchmark.md")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    corpus = load_corpus()
    if not corpus:
        raise SystemExit("no corpus images found under scripts/{photo,arxiv}-corpus/data")

    embedder = ClipEmbedder(args.device)

    encoded_rows = []
    labels: Dict[int, str] = {}
    for entry in corpus:
        vector = embedder.image_vector(entry["path"])
        row_id = stable_u64(entry["filename"])
        encoded_rows.append({
            "id": row_id,
            "vector": vector.tolist(),
            "text": entry["filename"],
        })
        labels[row_id] = entry["label"] or entry["filename"]

    text_vectors: Dict[str, np.ndarray] = {}
    for phrase in sorted({p for pair in QUERY_PAIRS for p in pair}):
        text_vectors[phrase] = embedder.text_vector(phrase)

    rows = []
    for pair_idx, (phrase_a, phrase_b) in enumerate(QUERY_PAIRS):
        vec_a = text_vectors[phrase_a]
        vec_b = text_vectors[phrase_b]
        sim = cosine(vec_a, vec_b)
        for threshold in thresholds_for(sim):
            ns = f"{args.namespace_prefix}-{pair_idx}-{str(threshold).replace('.', '-')}"
            delete_namespace(args.firn_url, ns, args.timeout)
            upsert_rows(args.firn_url, ns, encoded_rows, args.timeout)

            a_cold_ms, a_body = query_firn(
                args.firn_url, ns, vec_a, args.k, args.timeout, semantic_threshold=threshold
            )
            a_exact_ms, _ = query_firn(
                args.firn_url, ns, vec_a, args.k, args.timeout, semantic_threshold=threshold
            )
            before_b = metrics_for_namespace(args.firn_url, ns, args.timeout)
            b_sem_ms, b_sem_body = query_firn(
                args.firn_url, ns, vec_b, args.k, args.timeout, semantic_threshold=threshold
            )
            after_b = metrics_for_namespace(args.firn_url, ns, args.timeout)

            sem_hit = after_b["semantic_hits"] > before_b["semantic_hits"]
            sem_miss = after_b["semantic_misses"] > before_b["semantic_misses"]
            if sem_hit:
                b_true_ms, b_true_body = query_firn(args.firn_url, ns, vec_b, args.k, args.timeout)
            else:
                b_true_ms, b_true_body = b_sem_ms, b_sem_body

            a_ids = ids(a_body)
            b_sem_ids = ids(b_sem_body)
            b_true_ids = ids(b_true_body)
            rows.append({
                "phrase_a": phrase_a,
                "phrase_b": phrase_b,
                "cosine": sim,
                "threshold": threshold,
                "outcome": "semantic-hit" if sem_hit else "semantic-miss" if sem_miss else "other",
                "a_cold_ms": a_cold_ms,
                "a_exact_ms": a_exact_ms,
                "b_sem_ms": b_sem_ms,
                "b_true_ms": b_true_ms,
                "overlap": overlap(b_sem_ids, b_true_ids),
                "a_top": labels_for(a_ids[:3], labels),
                "b_reused_top": labels_for(b_sem_ids[:3], labels),
                "b_true_top": labels_for(b_true_ids[:3], labels),
            })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(args, corpus, rows), encoding="utf-8")
    print(f"wrote {out_path}")


def render_report(args, corpus: List[Dict[str, str]], rows: List[Dict]) -> str:
    hits = [r for r in rows if r["outcome"] == "semantic-hit"]
    misses = [r for r in rows if r["outcome"] == "semantic-miss"]
    hit_latencies = [r["b_sem_ms"] for r in hits]
    miss_latencies = [r["b_sem_ms"] for r in misses]
    exact_latencies = [r["a_exact_ms"] for r in rows]
    backend_latencies = [r["a_cold_ms"] for r in rows]

    lines = [
        "# Curated semantic cache benchmark",
        "",
        f"- **Firn URL**: `{args.firn_url}`",
        f"- **Namespace prefix**: `{args.namespace_prefix}`",
        f"- **Corpus**: {len(corpus)} images from the curated photo + arXiv corpora",
        f"- **CLIP model**: `openai/clip-vit-base-patch32`",
        f"- **Device**: `{args.device}`",
        f"- **k**: {args.k}",
        "",
        "## Summary",
        "",
        f"- Semantic hits: {len(hits)}",
        f"- Semantic misses: {len(misses)}",
        f"- Backend p50 from seed query: {pct(backend_latencies, 0.50):.2f} ms",
        f"- Exact-cache p50 from identical repeat: {pct(exact_latencies, 0.50):.2f} ms",
    ]
    if hit_latencies:
        lines.append(f"- Semantic-hit p50: {pct(hit_latencies, 0.50):.2f} ms")
    if miss_latencies:
        lines.append(f"- Semantic-miss p50: {pct(miss_latencies, 0.50):.2f} ms")

    lines.extend([
        "",
        "## Pair Results",
        "",
        "| query A | query B | cosine | threshold | outcome | A cold | A exact | B semantic | B true | overlap |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for r in rows:
        lines.append(
            "| {a} | {b} | {cos:.6f} | {thr:.6f} | {outcome} | {a_cold:.2f} ms | {a_exact:.2f} ms | {b_sem:.2f} ms | {b_true:.2f} ms | {overlap}/{k} |".format(
                a=r["phrase_a"],
                b=r["phrase_b"],
                cos=r["cosine"],
                thr=r["threshold"],
                outcome=r["outcome"],
                a_cold=r["a_cold_ms"],
                a_exact=r["a_exact_ms"],
                b_sem=r["b_sem_ms"],
                b_true=r["b_true_ms"],
                overlap=r["overlap"],
                k=args.k,
            )
        )

    lines.extend(["", "## Result Samples", ""])
    for r in rows:
        if r["outcome"] != "semantic-hit":
            continue
        lines.extend([
            f"### {r['phrase_a']} -> {r['phrase_b']} at threshold {r['threshold']:.6f}",
            "",
            f"- cosine: {r['cosine']:.6f}",
            f"- reused/true overlap: {r['overlap']}/{args.k}",
            "- reused top 3:",
        ])
        for item in r["b_reused_top"]:
            lines.append(f"  - {item}")
        lines.append("- true top 3:")
        for item in r["b_true_top"]:
            lines.append(f"  - {item}")
        lines.append("")

    lines.extend([
        "## Notes",
        "",
        "- The benchmark resets into a fresh namespace for each pair/threshold so exact and semantic cache state cannot leak between rows.",
        "- Query timings include the Firn HTTP call and result decode, but not CLIP encoding. Text and image embeddings are computed before the timed Firn calls.",
        "- The single-vector Metabare path stores the filename in Firn's text column. Labels in the samples come from the local corpus manifests for review only.",
    ])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
