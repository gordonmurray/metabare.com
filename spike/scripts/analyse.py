"""Summarise spike results against the plan's gates.

Reads results/reference.json and every results/<label>/*.json, then writes
results/summary.md with:

- timings, memory and bytes for every run;
- vector similarity of each run to the PyTorch reference;
- cross-backend agreement: corpus embedded by run A, queries by run B, top-10
  compared with the all-A ranking, for every ordered pair of warm runs;
- a small relevance check against the fixture categories;
- the gates for the candidate configuration, each marked pass, FAIL or
  INCOMPLETE when the evidence it needs is missing.

Result files may be plain JSON (as the page downloads them) or gzipped.
`--pack` first rewrites every plain .json result as .json.gz with vector
components rounded to six decimal places, a relative change near 1e-5 against
the 1e-2 differences being measured, so raw results are small enough to commit.

Standard library only: python3 scripts/analyse.py [--pack]
"""

from __future__ import annotations

import gzip
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
TOP_K = 10

# The configuration the gates are evaluated for: CLIP precision, then the
# MiniLM embedder's precision.
CANDIDATE = ("q4f16", "q8")

BUDGET = {
    "download_mb": 150,
    "webgpu_image_ms": 300,
    "wasm_image_ms": 2000,
    "wasm_query_ms": 500,
    "memory_mb": 1500,
    "agreement_mean": 0.9,
    "agreement_min": 0.7,
}

# Which ONNX file each precision loads, as named in models.lock.json.
SUFFIX = {"fp32": "", "fp16": "_fp16", "q8": "_quantized", "q4f16": "_q4f16"}
DIMS = {"images": 512, "image_queries": 512, "notes": 384, "note_queries": 384}
ORT_WASM = ROOT / "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.asyncify.wasm"
MOBILE_MARKERS = ("Android", "iPhone", "iPad", "Mobile")


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def ranking(corpus: list[list[float]], query: list[float]) -> list[int]:
    # Vectors are unit length, so the dot product is cosine similarity.
    scores = [dot(query, c) for c in corpus]
    return sorted(range(len(corpus)), key=lambda i: -scores[i])


def agreement(a: dict, b: dict, corpus: str, queries: str) -> tuple[float, float]:
    """Corpus from a, queries from b, compared with corpus and queries from a."""
    overlaps = []
    for qa, qb in zip(a["vectors"][queries], b["vectors"][queries], strict=True):
        base = set(ranking(a["vectors"][corpus], qa)[:TOP_K])
        mixed = set(ranking(a["vectors"][corpus], qb)[:TOP_K])
        overlaps.append(len(base & mixed) / TOP_K)
    return sum(overlaps) / len(overlaps), min(overlaps)


def similarity(a: dict, b: dict, key: str) -> tuple[float, float]:
    sims = [dot(x, y) for x, y in zip(a["vectors"][key], b["vectors"][key], strict=True)]
    return sum(sims) / len(sims), min(sims)


def relevance(run: dict, fx: dict, kind: str) -> float:
    """Mean recall at R, where R is the number of relevant items per query."""
    if kind == "images":
        items, qs, field = fx["images"], fx["image_queries"], "category"
        corpus, queries = "images", "image_queries"
    else:
        items, qs, field = fx["notes"], fx["note_queries"], "topic"
        corpus, queries = "notes", "note_queries"
    scores = []
    for q, qv in zip(qs, run["vectors"][queries], strict=True):
        rel = {i for i, it in enumerate(items) if it[field] == q["relevant"]}
        top = ranking(run["vectors"][corpus], qv)[: len(rel)]
        scores.append(len(rel & set(top)) / len(rel))
    return sum(scores) / len(scores)


def validate(run: dict, fx: dict, name: str) -> None:
    """Refuse to compare runs that did not embed the same fixtures."""
    expected = {
        "images": len(fx["images"]),
        "image_queries": len(fx["image_queries"]),
        "notes": len(fx["notes"]),
        "note_queries": len(fx["note_queries"]),
    }
    if "fixtures_seed" in run and run["fixtures_seed"] != fx["seed"]:
        raise SystemExit(f"{name}: fixtures seed {run['fixtures_seed']} != {fx['seed']}")
    for key, count in expected.items():
        rows = run["vectors"][key]
        if len(rows) != count:
            raise SystemExit(f"{name}: {key} has {len(rows)} vectors, expected {count}")
        for row in rows:
            if len(row) != DIMS[key] or not all(math.isfinite(v) for v in row):
                raise SystemExit(
                    f"{name}: {key} has a vector of the wrong size or a non-finite value"
                )


def candidate_files(clip: str, embed: str) -> list[Path]:
    lock = json.loads((ROOT / "models.lock.json").read_text())
    files = []
    for e in lock:
        f = e["file"]
        wanted = (
            not f.startswith("onnx/")
            or (
                "clip" in e["repo"]
                and f
                in (f"onnx/vision_model{SUFFIX[clip]}.onnx", f"onnx/text_model{SUFFIX[clip]}.onnx")
            )
            or ("MiniLM" in e["repo"] and f == f"onnx/model{SUFFIX[embed]}.onnx")
        )
        if wanted:
            files.append(ROOT / "public" / "models" / e["repo"] / f)
    return files


def gzip_mb(paths: list[Path]) -> float | None:
    """Size after gzip -6: an estimate of what pre-compressed hosting would serve."""
    if not all(p.exists() for p in paths):
        return None
    return sum(len(gzip.compress(p.read_bytes(), compresslevel=6)) for p in paths) / 1e6


def is_mobile(run: dict) -> bool:
    return any(m in run["env"]["user_agent"] for m in MOBILE_MARKERS)


def cell(status: str) -> str:
    return {"pass": "pass", "fail": "**FAIL**", "incomplete": "*INCOMPLETE*"}[status]


def load(path: Path) -> dict:
    if path.suffix == ".gz":
        return json.loads(gzip.decompress(path.read_bytes()))
    return json.loads(path.read_text())


def pack() -> None:
    for path in [RESULTS / "reference.json", *RESULTS.glob("*/*.json")]:
        if not path.exists():
            continue
        run = json.loads(path.read_text())
        for key, rows in run["vectors"].items():
            run["vectors"][key] = [[round(v, 6) for v in row] for row in rows]
        packed = json.dumps(run, separators=(",", ":")).encode()
        path.with_name(path.name + ".gz").write_bytes(
            gzip.compress(packed, compresslevel=9, mtime=0)
        )
        path.unlink()
        print(f"packed {path.relative_to(ROOT)}")


def main() -> None:
    if "--pack" in sys.argv:
        pack()
    fx = json.loads((ROOT / "public" / "fixtures" / "fixtures.json").read_text())
    ref_path = RESULTS / "reference.json"
    ref = load(ref_path if ref_path.exists() else ref_path.with_name("reference.json.gz"))
    ref["_name"] = "reference"
    validate(ref, fx, "reference")

    runs = []
    for path in sorted([*RESULTS.glob("*/*.json"), *RESULTS.glob("*/*.json.gz")]):
        r = load(path)
        stem = path.name.removesuffix(".gz").removesuffix(".json")
        r["_name"] = f"{path.parent.name}/{stem}"
        r["_machine"] = f"{path.parent.name}/{stem.split('-')[0]}"
        validate(r, fx, r["_name"])
        runs.append(r)
    warm = [r for r in runs if not r["cold"]]
    cold = [r for r in runs if r["cold"]]

    out = [
        "# Spike results",
        "",
        "Generated by `scripts/analyse.py` from the JSON files in this directory. Budgets are",
        "from the plan's Gates section. Run names are `<machine>/<browser>-<device>-<CLIP",
        "precision>[-embed-<embedder precision>][-cold]`. A cold run starts from a fresh",
        "browser profile; the warm run follows it in the same profile.",
        "",
        "## Runs",
        "",
        "Load is the time to construct every model and session. Image and query times exclude",
        "fetching the fixture and are medians excluding the first item. A query is one",
        "text through both the CLIP text encoder and the MiniLM embedder. Network MB is the",
        "transfer size of model and runtime files; files read from the Cache API do not count.",
        "Process MB is the largest sum of proportional set size over the browser's whole",
        "process tree, GPU process included, sampled every 500 ms from /proc by",
        "the test driver (Linux only). In-page MB is the largest",
        "`performance.measureUserAgentSpecificMemory()` sample (Chromium only); it reported",
        "a few megabytes while the worker held the models, so it is recorded but not used for",
        "the memory gate.",
        "",
        "| Run | Fallback | Threads | Load ms | Network MB | Image ms | Query ms | Note ms "
        "| Process MB | In-page MB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        t, b = r["timings"], r["bytes"]
        mem = (r.get("memory") or {}).get("max_bytes")
        proc = (r.get("process_memory") or {}).get("max_bytes")
        net = (b["models_network"] + b["runtime_network"]) / 1e6
        out.append(
            f"| {r['_name']} | {r['fallback'] or '-'} | {r['env']['wasm_threads']} | {t['load_ms']:.0f} "
            f"| {net:.1f} | {t['image']['median_rest_ms']:.1f} | {t['query_both']['median_rest_ms']:.1f} "
            f"| {t['note']['median_rest_ms']:.1f} | {f'{proc / 1e6:.0f}' if proc else '-'} "
            f"| {f'{mem / 1e6:.0f}' if mem else '-'} |"
        )
    out += ["", "| Machine/browser | User agent | WebGPU adapter |", "| --- | --- | --- |"]
    for key in sorted({r["_machine"] for r in runs}):
        sample = next(r for r in runs if r["_machine"] == key)
        out.append(
            f"| {key} | {sample['env']['user_agent']} | {sample['env']['webgpu']['adapter'] or 'none'} |"
        )

    out += [
        "",
        "## Similarity to the PyTorch reference",
        "",
        "Cosine similarity between each run's vector and the reference vector for the same input,",
        "as mean / minimum.",
        "",
        "| Run | Images | CLIP text | Notes | Note queries |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in warm:
        pairs = (similarity(r, ref, k) for k in DIMS)
        out.append(
            f"| {r['_name']} | " + " | ".join(f"{m:.4f} / {lo:.4f}" for m, lo in pairs) + " |"
        )

    pool = [*warm, ref]
    for corpus, queries in [("images", "image_queries"), ("notes", "note_queries")]:
        out += [
            "",
            f"## Cross-backend agreement, {corpus}",
            "",
            f"Row A embeds the corpus, column B embeds the queries. Each cell is the top-{TOP_K}",
            "overlap with A's own ranking, as mean / minimum over queries. Budget: mean at least",
            f"{BUDGET['agreement_mean']}, no query below {BUDGET['agreement_min']}. Failing cells have an asterisk.",
            "",
            "| A \\ B | " + " | ".join(str(i) for i in range(len(pool))) + " |",
            "| --- " * (len(pool) + 1) + "|",
        ]
        for i, a in enumerate(pool):
            cells = []
            for b in pool:
                if a is b:
                    cells.append("-")
                    continue
                m, lo = agreement(a, b, corpus, queries)
                bad = m < BUDGET["agreement_mean"] or lo < BUDGET["agreement_min"]
                cells.append(f"{m:.2f} / {lo:.1f}{'*' if bad else ''}")
            out.append(f"| {i}: {a['_name']} | " + " | ".join(cells) + " |")

    out += [
        "",
        "## Relevance on the fixtures",
        "",
        "Recall at R against the fixture categories. Synthetic and small: a sanity check, not an",
        "evaluation.",
        "",
        "| Run | Images | Notes |",
        "| --- | --- | --- |",
    ]
    for r in [ref, *warm]:
        out.append(
            f"| {r['_name']} | {relevance(r, fx, 'images'):.3f} | {relevance(r, fx, 'notes'):.3f} |"
        )

    clip, embed = CANDIDATE
    cand = [r for r in warm if (r["dtype"], r["embed_dtype"]) == CANDIDATE]
    gpu = [r for r in cand if r["device"] == "webgpu" and r["requested_device"] == "webgpu"]
    cpu = [r for r in cand if r["device"] == "wasm"]
    gates: list[tuple[str, str, str, str, str]] = []

    # Download: payload of a real cold load, and a gzip estimate of the same files.
    cold_cand = [r for r in cold if (r["dtype"], r["embed_dtype"]) == CANDIDATE]
    if cold_cand:
        r = cold_cand[0]
        payload = (r["bytes"]["models_payload"] + r["bytes"]["runtime_payload"]) / 1e6
        gates.append(
            (
                "First-visit models and runtime, uncompressed",
                r["_name"],
                f"{payload:.1f} MB",
                f"{BUDGET['download_mb']} MB",
                "pass" if payload <= BUDGET["download_mb"] else "fail",
            )
        )
    else:
        gates.append(
            (
                "First-visit models and runtime, uncompressed",
                "-",
                "no cold run",
                f"{BUDGET['download_mb']} MB",
                "incomplete",
            )
        )
    est = gzip_mb([*candidate_files(clip, embed), ORT_WASM])
    gates.append(
        (
            "Same files after gzip -6 (estimate)",
            "-",
            "files not present" if est is None else f"{est:.1f} MB",
            f"{BUDGET['download_mb']} MB",
            "incomplete" if est is None else ("pass" if est <= BUDGET["download_mb"] else "fail"),
        )
    )

    for label, group, limit in [
        ("WebGPU", gpu, BUDGET["webgpu_image_ms"]),
        ("WASM", cpu, BUDGET["wasm_image_ms"]),
    ]:
        if not group:
            gates.append((f"Image embedding, {label}", "-", "no run", f"{limit} ms", "incomplete"))
        for r in group:
            v = r["timings"]["image"]["median_rest_ms"]
            gates.append(
                (
                    f"Image embedding, {label}",
                    r["_name"],
                    f"{v:.1f} ms",
                    f"{limit} ms",
                    "pass" if v <= limit else "fail",
                )
            )
    for r in cpu or [None]:
        if r is None:
            gates.append(
                (
                    "Query, both encoders, WASM",
                    "-",
                    "no run",
                    f"{BUDGET['wasm_query_ms']} ms",
                    "incomplete",
                )
            )
            continue
        v = r["timings"]["query_both"]["median_rest_ms"]
        gates.append(
            (
                "Query, both encoders, WASM",
                r["_name"],
                f"{v:.1f} ms",
                f"{BUDGET['wasm_query_ms']} ms",
                "pass" if v <= BUDGET["wasm_query_ms"] else "fail",
            )
        )
    for r in cand:
        mem = (r.get("process_memory") or {}).get("max_bytes")
        if mem is None:
            gates.append(
                (
                    "Peak memory",
                    r["_name"],
                    "not measured on this platform",
                    f"{BUDGET['memory_mb']} MB",
                    "incomplete",
                )
            )
        else:
            gates.append(
                (
                    "Peak memory",
                    r["_name"],
                    f"{mem / 1e6:.0f} MB",
                    f"{BUDGET['memory_mb']} MB",
                    "pass" if mem / 1e6 <= BUDGET["memory_mb"] else "fail",
                )
            )

    # Agreement needs a genuine WebGPU run and a WASM run, in both directions.
    if not gpu or not cpu:
        gates.append(
            ("Cross-backend agreement", "-", "needs a WebGPU and a WASM run", "-", "incomplete")
        )
    for a in gpu:
        for b in cpu:
            for x, y in [(a, b), (b, a)]:
                for corpus, queries in [("images", "image_queries"), ("notes", "note_queries")]:
                    m, lo = agreement(x, y, corpus, queries)
                    ok = m >= BUDGET["agreement_mean"] and lo >= BUDGET["agreement_min"]
                    gates.append(
                        (
                            f"Agreement, {corpus}",
                            f"{x['_name']} corpus, {y['_name']} queries",
                            f"{m:.3f} / {lo:.1f}",
                            f"{BUDGET['agreement_mean']} / {BUDGET['agreement_min']}",
                            "pass" if ok else "fail",
                        )
                    )

    phones = [r for r in cand if is_mobile(r)]
    gates.append(
        (
            "Phone loads models and embeds",
            ", ".join(r["_name"] for r in phones) or "-",
            "ran" if phones else "no phone run",
            "completes",
            "pass" if phones else "incomplete",
        )
    )

    out += [
        "",
        f"## Gates for the candidate: CLIP {clip}, embedder {embed}",
        "",
        "| Gate | Run | Value | Budget | Result |",
        "| --- | --- | --- | --- | --- |",
        *(f"| {g} | {run} | {v} | {b} | {cell(s)} |" for g, run, v, b, s in gates),
    ]
    statuses = {s for *_, s in gates}
    overall = (
        "fail" if "fail" in statuses else ("incomplete" if "incomplete" in statuses else "pass")
    )
    out += ["", f"Overall: {cell(overall)}."]

    (RESULTS / "summary.md").write_text("\n".join(out) + "\n")
    print(f"wrote {RESULTS / 'summary.md'}; overall {overall}")


if __name__ == "__main__":
    main()
