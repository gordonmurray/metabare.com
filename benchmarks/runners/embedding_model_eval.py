#!/usr/bin/env python3
"""Compare candidate CPU text embedding models.

The text model should be chosen from measurements rather than from reputation,
and no model is better than another until that has been checked. This is that
evaluation.

**What this measures well**: operational characteristics. Model size on disk,
cold load time, per-query CPU encode latency, and vector dimension. These
directly affect container image size, pod start time and the cost of the stable
node, and they are measured on the actual runtime (ONNX Runtime, CPU, one
thread) rather than inferred.

**What this measures weakly**: retrieval quality. The corpus is 24 synthetic
documents with 12 queries. That is enough to catch a model that is obviously
unsuited to technical English, and nowhere near enough to separate models that
differ by a couple of points on a real benchmark. Treat the ranking as a
tiebreaker behind licence, size and latency, and do not quote these numbers as
though they were MTEB scores.

    uv run python benchmarks/runners/embedding_model_eval.py

Results are written to benchmarks/results/embedding-model-eval.json.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from metabare.config import EncoderSettings
from metabare.embeddings import Pooling, TextEncoder

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPO_ROOT / "benchmarks" / "datasets" / "technical-notes" / "corpus.json"
RESULTS_PATH = REPO_ROOT / "benchmarks" / "results" / "embedding-model-eval.json"
CACHE_DIR = REPO_ROOT / "models" / "cache"


@dataclass(frozen=True)
class Candidate:
    """A model to evaluate.

    ``pooling`` and ``query_prefix`` are properties of how the model was
    trained, not preferences. Using the wrong pooling produces embeddings that
    look fine and retrieve badly, which is the failure this evaluation would
    otherwise silently reward.
    """

    model_id: str
    licence: str
    dimension: int
    pooling: Pooling
    query_prefix: str
    passage_prefix: str = ""
    onnx_file: str = "onnx/model.onnx"


CANDIDATES = [
    Candidate(
        model_id="BAAI/bge-small-en-v1.5",
        licence="MIT",
        dimension=384,
        pooling=Pooling.CLS,
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
    Candidate(
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        licence="Apache-2.0",
        dimension=384,
        pooling=Pooling.MEAN,
        query_prefix="",
    ),
    Candidate(
        model_id="intfloat/e5-small-v2",
        licence="MIT",
        dimension=384,
        pooling=Pooling.MEAN,
        query_prefix="query: ",
        passage_prefix="passage: ",
        # This repo publishes its export at the root, not under onnx/. Worth
        # noting as a small packaging inconsistency between otherwise similar
        # models: a deployment script that assumes one layout breaks on the
        # other.
        onnx_file="model.onnx",
    ),
    Candidate(
        model_id="BAAI/bge-base-en-v1.5",
        licence="MIT",
        dimension=768,
        pooling=Pooling.CLS,
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
]


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Binary-gain nDCG@k. With one relevant document this reduces to 1/log2(rank+1)."""
    gains = [1.0 if doc in relevant else 0.0 for doc in ranked[:k]]
    dcg = sum(g / np.log2(i + 2) for i, g in enumerate(gains))
    ideal = sum(1.0 / np.log2(i + 2) for i in range(min(len(relevant), k)))
    return float(dcg / ideal) if ideal else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for index, doc in enumerate(ranked, start=1):
        if doc in relevant:
            return 1.0 / index
    return 0.0


def evaluate(candidate: Candidate, corpus: dict[str, Any]) -> dict[str, Any]:
    documents = corpus["documents"]
    queries = corpus["queries"]

    settings = EncoderSettings(
        model_id=candidate.model_id,
        model_revision="main",
        onnx_file=candidate.onnx_file,
        dimension=candidate.dimension,
        cache_dir=str(CACHE_DIR),
        query_prefix=candidate.query_prefix,
        passage_prefix=candidate.passage_prefix,
        pooling=candidate.pooling.value,
        intra_op_threads=1,
    )
    encoder = TextEncoder(settings, pooling=candidate.pooling)

    load_started = time.perf_counter()
    encoder.load()
    load_seconds = time.perf_counter() - load_started

    index_started = time.perf_counter()
    document_vectors = encoder.encode_passages([d["text"] for d in documents])
    index_seconds = time.perf_counter() - index_started

    # Query latency is measured one query at a time, because that is how the
    # search path actually calls it. A batched figure would flatter it.
    latencies: list[float] = []
    query_vectors = []
    for query in queries:
        started = time.perf_counter()
        vector = encoder.encode_query(query["text"])
        latencies.append((time.perf_counter() - started) * 1000)
        query_vectors.append(vector)

    doc_ids = [d["id"] for d in documents]
    recall_at_1: list[float] = []
    recall_at_3: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    misses: list[dict[str, Any]] = []

    for query, vector in zip(queries, query_vectors, strict=True):
        # Vectors are L2-normalised, so a dot product is cosine similarity.
        scores = document_vectors @ vector
        order = np.argsort(-scores)
        ranked = [doc_ids[i] for i in order]
        relevant = set(query["relevant"])

        recall_at_1.append(1.0 if ranked[0] in relevant else 0.0)
        recall_at_3.append(1.0 if relevant & set(ranked[:3]) else 0.0)
        reciprocal_ranks.append(reciprocal_rank(ranked, relevant))
        ndcgs.append(ndcg_at_k(ranked, relevant, 5))

        if ranked[0] not in relevant:
            misses.append(
                {
                    "query": query["text"],
                    "expected": sorted(relevant),
                    "got": ranked[0],
                    "rank_of_expected": next(
                        (i for i, d in enumerate(ranked, 1) if d in relevant), None
                    ),
                }
            )

    model_bytes = sum(
        path.stat().st_size
        for path in CACHE_DIR.rglob("*.onnx")
        if candidate.model_id.split("/")[-1] in str(path)
    )

    return {
        "model_id": candidate.model_id,
        "licence": candidate.licence,
        "dimension": candidate.dimension,
        "pooling": candidate.pooling.value,
        "uses_query_prefix": bool(candidate.query_prefix),
        "onnx_bytes": model_bytes,
        "load_seconds": round(load_seconds, 3),
        "index_seconds_for_corpus": round(index_seconds, 3),
        "query_latency_ms": {
            "p50": round(statistics.median(latencies), 2),
            "mean": round(statistics.fmean(latencies), 2),
            "max": round(max(latencies), 2),
        },
        "retrieval": {
            "recall_at_1": round(statistics.fmean(recall_at_1), 3),
            "recall_at_3": round(statistics.fmean(recall_at_3), 3),
            "mrr": round(statistics.fmean(reciprocal_ranks), 3),
            "ndcg_at_5": round(statistics.fmean(ndcgs), 3),
        },
        "misses": misses,
    }


def main() -> int:
    corpus = json.loads(CORPUS_PATH.read_text())
    print(f"Corpus: {len(corpus['documents'])} documents, {len(corpus['queries'])} queries")
    print("CPU, ONNX Runtime, one intra-op thread.\n")

    results = []
    for candidate in CANDIDATES:
        print(f"--- {candidate.model_id}")
        try:
            result = evaluate(candidate, corpus)
        except Exception as exc:  # noqa: BLE001 - a candidate failing is a result
            print(f"    FAILED: {exc}\n")
            results.append({"model_id": candidate.model_id, "error": str(exc)})
            continue
        r = result["retrieval"]
        print(
            f"    recall@1 {r['recall_at_1']:.3f}  recall@3 {r['recall_at_3']:.3f}  "
            f"MRR {r['mrr']:.3f}  nDCG@5 {r['ndcg_at_5']:.3f}"
        )
        print(
            f"    query p50 {result['query_latency_ms']['p50']:.1f} ms  "
            f"load {result['load_seconds']:.2f} s  "
            f"onnx {result['onnx_bytes'] / 1e6:.0f} MB  {result['licence']}\n"
        )
        results.append(result)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "corpus": {
                    "name": corpus["name"],
                    "version": corpus["version"],
                    "documents": len(corpus["documents"]),
                    "queries": len(corpus["queries"]),
                },
                "runtime": "onnxruntime CPUExecutionProvider, intra_op_num_threads=1",
                "candidates": [asdict(c) | {"pooling": c.pooling.value} for c in CANDIDATES],
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {RESULTS_PATH.relative_to(REPO_ROOT)}")
    print(
        "\nReminder: 24 documents cannot rank models on relevance. Use these numbers "
        "for latency, size and licence, and as a sanity check only."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
