# Browser inference spike

Can a browser embed images and text well enough, fast enough and consistently
enough for MetaBare to drop server-side inference? This directory answers
that for one candidate pair of models before anything else is built.

- Image and query encoder: CLIP ViT-B/32 (`Xenova/clip-vit-base-patch32`, ONNX
  export of `openai/clip-vit-base-patch32`), 512 dimensions.
- Text embedder for notes: all-MiniLM-L6-v2 (`Xenova/all-MiniLM-L6-v2`),
  384 dimensions, Apache-2.0.
- Runtime: Transformers.js 4.3.1 with its pinned ONNX Runtime Web build, in a
  Web Worker, on WebGPU or WASM.

## What it measures

For each device, backend and precision, on 50 synthetic images, 30 notes and
40 queries:

- bytes fetched on a cold load, and load time cold and warm;
- per-item embedding latency, first and median of the rest, excluding the
  fixture download; a query is timed through both encoders;
- peak memory: proportional set size over the browser's whole process tree,
  GPU process included, read from `/proc` by the test driver (Linux only);
- cosine similarity of every vector to a PyTorch reference;
- **cross-backend agreement**: the corpus embedded on one backend and the
  queries on another, compared by top-10 overlap with a single-backend
  ranking. This is the measure that matters: a library embedded on a laptop
  GPU has to be searchable from a phone running WASM.

The fixtures exist to give the encoders varied input. They are not a relevance
evaluation.

## Findings so far

On a laptop with Intel Iris Xe graphics (Linux, Chrome 154 and Firefox 155),
measured 2026-10-07. Every number is in [`results/summary.md`](results/summary.md),
generated from the raw runs beside it.

| Precision (CLIP / MiniLM) | Download | Image, WebGPU | Image, WASM | Interoperates with other precisions |
| --- | --- | --- | --- | --- |
| fp32 / fp32 | 726 MB | 58 ms | 81 ms | yes |
| fp16 / fp16 | 379 MB | 50 ms | 86 ms | yes |
| q8 / q8 | 207 MB | 252 ms | 57 ms | **no** |
| q4f16 / q8 | 179 MB | 232 ms | 125 ms | yes |

Download is a cold load of models and the ONNX Runtime WASM file,
uncompressed. Image times are warm medians.

- **8-bit CLIP drifts.** Its vectors sit further from the reference (cosine
  0.92, minimum 0.85) than any other precision. WebGPU and WASM at q8 agree
  with each other, but a library embedded at q8 and searched at any other
  precision, or the reverse, fails the agreement gate, and its fixture
  relevance is the lowest measured. Every device would have to use q8, and
  moving to another precision later would mean re-embedding the whole
  library, so it is ruled out.
- **q4f16 CLIP with q8 MiniLM passes** agreement across WebGPU and WASM, in
  both directions and across Chrome and Firefox, and passes the latency and
  memory budgets. It is 179 MB uncompressed against a 150 MB budget; gzip
  brings the same files to an estimated 140 MB, which the hosting item has to
  confirm, because CloudFront does not compress objects over 10 MB.
- **WebGPU is not always faster.** On this integrated GPU, quantised models
  run faster on WASM than on WebGPU; fp16 is the fast WebGPU path.
- **A query takes about 27 ms on WASM** through both encoders, against a
  500 ms budget.
- **Peak memory fails the 1.5 GB budget on some Firefox first visits.** Over
  the whole browser process tree, warm loads and Chrome's cold loads stay at
  1.05 to 1.35 GB. Three of eight cold Firefox runs, on either path, peaked
  at 1.67 to 1.70 GB; the other five at 1.27 to 1.37 GB. The cache is not the
  cause, and under a hard memory limit the first load did not complete within
  1.6 GB: see [`results/memory/`](results/memory/README.md). Fixing it is
  assigned to the ingestion work.
- Not yet measured: a phone, a discrete GPU, Safari.

## Browser support

Stable releases. What the spike has run on, and what the
[WebGPU implementation status](https://github.com/gpuweb/gpuweb/wiki/Implementation-Status)
page (updated 2026-10-02) says about the rest. WASM is the fallback wherever
WebGPU is missing; it needs `SharedArrayBuffer`, which the page gets from
cross-origin isolation.

| Browser | WebGPU | Tested here |
| --- | --- | --- |
| Chrome, Edge 113+ on Windows x86/x64, macOS, ChromeOS | Yes | No |
| Chrome, Edge on Windows ARM64 | Behind a flag | No |
| Chrome 144+ on Linux, Intel Gen12+ GPUs | Yes | Chrome 154, Iris Xe: WebGPU and WASM |
| Chrome 147+ on Linux, NVIDIA driver 535.183.01+ on Wayland | Yes | No |
| Chrome on Linux, other GPUs | Behind a flag | No |
| Chrome 121+ on Android 12+, ARM, Qualcomm and Intel GPUs | Yes | No |
| Chrome 139+ on Android 16+, Imagination GPUs | Yes | No |
| Firefox 141+ on Windows | Yes | No |
| Firefox 145+ on macOS 26, Apple Silicon; 147+ on any macOS, Apple Silicon | Yes | No |
| Firefox on Intel macOS | Nightly only | No |
| Firefox on Linux, stable | No (Nightly only) | Firefox 155, Linux: falls back to WASM |
| Firefox on Android, stable | No (flagged in Beta and Nightly) | No |
| Safari 26+ on macOS, iOS, iPadOS | Yes | No; WebKit WASM path runs in CI |
| Safari before 26 | No | No |

## Run it

```bash
npm ci
../models/fetch.sh               # about 1.3 GB, verified against models/models.lock.json
npm run dev                      # http://localhost:5173
```

The page takes its settings from the form or the query string, runs in a
worker, and offers the result as a JSON download.

Automated, on this machine's Chrome (WebGPU) and on bundled Chromium (WASM):

```bash
SPIKE_CHANNEL=chrome SPIKE_LABEL=<machine> \
  SPIKE_RUNS=webgpu:q4f16:q8,wasm:q4f16:q8 npx playwright test
python3 scripts/analyse.py --pack  # compresses new results, writes results/summary.md
```

Each entry in `SPIKE_RUNS` is `device:clipPrecision[:embedderPrecision]`, and
runs twice in one browser profile: once with the cache cleared, once warm.

### On a phone

WebGPU and cross-origin isolation need a secure context, which plain HTTP to
a LAN address is not.

```bash
npm run dev:lan                  # HTTPS with a self-signed certificate
```

Open the printed network address on the phone, accept the certificate warning,
run, and copy the downloaded JSON into `results/<phone-model>/`, renamed to
start with the browser, for example `chrome-wasm-q4f16-embed-q8.json`.

### Reference vectors

```bash
uv run --no-project --index https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match \
  --with torch==2.14.1 --with transformers==5.19.0 \
  --with sentence-transformers==6.1.0 --with pillow==12.3.0 \
  python scripts/reference.py
```

### Fixtures

```bash
uv run --no-project --with pillow==12.3.0 python scripts/make_fixtures.py
```

Deterministic on one machine with the same Pillow and font; PNG bytes may
differ elsewhere, which is why the images are committed.

## Files

| Path | What |
| --- | --- |
| `src/worker.ts` | Loads the models and embeds everything |
| `src/main.ts` | Page, settings, memory sampling, result download |
| `tests/spike.spec.ts` | Playwright driver for the matrix |
| `scripts/reference.py` | PyTorch reference vectors |
| `scripts/analyse.py` | Agreement, similarity and gates into `results/summary.md` |
| `results/` | Raw runs (gzipped JSON) and the generated summary |
