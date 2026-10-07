// Runs the models off the main thread. One message in (the run settings), one
// message out (vectors, timings and environment), plus progress messages.

import {
  AutoProcessor,
  AutoTokenizer,
  CLIPTextModelWithProjection,
  CLIPVisionModelWithProjection,
  RawImage,
  env,
  pipeline,
  type Tensor,
} from "@huggingface/transformers";
import type { Device, Fixtures, RunRequest, RunResult, Timing, WorkerMessage } from "./types";

const CLIP = "Xenova/clip-vit-base-patch32";
const MINILM = "Xenova/all-MiniLM-L6-v2";

// Everything comes from this origin. Without the wasmPaths override,
// Transformers.js fetches ONNX Runtime from a public CDN.
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = "/models/";
// The build choice follows Transformers.js's own default, whose detection is
// not exported: the plain build for Safari before 26 without WebGPU, the
// asyncify build everywhere else.
function safariBelow26(): boolean {
  const ua = navigator.userAgent;
  const safari =
    (navigator.vendor || "").includes("Apple") &&
    !/CriOS|FxiOS|EdgiOS|OPiOS|mercury|brave/i.test(ua) &&
    !ua.includes("Chrome") &&
    !ua.includes("Android");
  const version = ua.match(/Version\/(\d+)/);
  return safari && version !== null && Number(version[1]) < 26;
}
const onnx = env.backends.onnx;
const ortBuild = safariBelow26() && !("gpu" in navigator) ? "" : ".asyncify";
if (onnx.wasm) {
  onnx.wasm.wasmPaths = {
    mjs: `/ort/ort-wasm-simd-threaded${ortBuild}.mjs`,
    wasm: `/ort/ort-wasm-simd-threaded${ortBuild}.wasm`,
  };
}

const post = (m: WorkerMessage) => self.postMessage(m);
const log = (text: string) => post({ type: "progress", text });

function toRows(t: Tensor): number[][] {
  const f = t.type === "float32" ? t : t.to("float32");
  const [n, d] = f.dims as [number, number];
  const data = f.data as Float32Array;
  const rows: number[][] = [];
  for (let i = 0; i < n; i++) {
    const row = Array.from(data.subarray(i * d, (i + 1) * d));
    const norm = Math.hypot(...row);
    rows.push(row.map((v) => v / norm));
  }
  return rows;
}

function summarise(samples: number[]): Timing {
  const rest = samples.slice(1).sort((a, b) => a - b);
  const median = rest.length ? rest[Math.floor(rest.length / 2)] : samples[0];
  return { first_ms: samples[0], median_rest_ms: median, samples_ms: samples };
}

// Probing the GPU must never stop a run: a forced WASM run, or a fallback,
// still has to work when the adapter request throws.
async function gpuInfo(): Promise<RunResult["env"]["webgpu"]> {
  const gpu = (navigator as Navigator & { gpu?: GPU }).gpu;
  if (!gpu) return { available: false, adapter: null, shader_f16: false, error: null };
  try {
    const adapter = await gpu.requestAdapter({ powerPreference: "high-performance" });
    if (!adapter) return { available: true, adapter: null, shader_f16: false, error: null };
    const info = adapter.info;
    return {
      available: true,
      adapter: `${info.vendor} ${info.architecture} ${info.device} ${info.description}`.trim(),
      shader_f16: adapter.features.has("shader-f16"),
      error: null,
    };
  } catch (err) {
    return { available: true, adapter: null, shader_f16: false, error: String(err) };
  }
}

// transferSize is what crossed the network (0 for an HTTP cache hit);
// decodedBodySize is the payload whether or not it came from a cache. Model
// files read back from the Cache API produce no resource entry at all.
function bytes(): RunResult["bytes"] {
  const entries = performance.getEntriesByType("resource") as PerformanceResourceTiming[];
  const sum = (filter: (e: PerformanceResourceTiming) => boolean, key: "transferSize" | "decodedBodySize") =>
    entries.filter(filter).reduce((s, e) => s + e[key], 0);
  const model = (e: PerformanceResourceTiming) => e.name.includes("/models/");
  const runtime = (e: PerformanceResourceTiming) => e.name.includes("/ort/");
  return {
    models_network: sum(model, "transferSize"),
    models_payload: sum(model, "decodedBodySize"),
    runtime_network: sum(runtime, "transferSize"),
    runtime_payload: sum(runtime, "decodedBodySize"),
  };
}

type Models = Awaited<ReturnType<typeof loadModels>>;

async function loadModels(device: Device, req: RunRequest, loaded: { dispose(): Promise<unknown> }[]) {
  const processor = await AutoProcessor.from_pretrained(CLIP);
  const tokenizer = await AutoTokenizer.from_pretrained(CLIP);
  const vision = await CLIPVisionModelWithProjection.from_pretrained(CLIP, { device, dtype: req.dtype });
  loaded.push(vision);
  const text = await CLIPTextModelWithProjection.from_pretrained(CLIP, { device, dtype: req.dtype });
  loaded.push(text);
  const embed = await pipeline("feature-extraction", MINILM, { device, dtype: req.embed_dtype });
  loaded.push(embed);
  return { processor, tokenizer, vision, text, embed };
}

async function clipText(m: Models, text: string): Promise<number[]> {
  const { text_embeds } = await m.text(m.tokenizer([text], { padding: true, truncation: true }));
  return toRows(text_embeds)[0];
}

async function miniLm(m: Models, text: string): Promise<number[]> {
  return toRows(await m.embed(text, { pooling: "mean", normalize: true }))[0];
}

// Loading and every inference run as one attempt, so a WebGPU failure at any
// point, not only while loading, falls back to a complete WASM attempt.
async function attempt(device: Device, req: RunRequest, fx: Fixtures, imageBlobs: Blob[]) {
  const loaded: { dispose(): Promise<unknown> }[] = [];
  try {
    log(`loading models on ${device} / ${req.dtype}, embedder ${req.embed_dtype}`);
    const t0 = performance.now();
    const m = await loadModels(device, req, loaded);
    const loadMs = performance.now() - t0;

    log("embedding images");
    const imageVecs: number[][] = [];
    const imageMs: number[] = [];
    for (const blob of imageBlobs) {
      const s = performance.now();
      const inputs = await m.processor(await RawImage.fromBlob(blob));
      const { image_embeds } = await m.vision(inputs);
      imageVecs.push(toRows(image_embeds)[0]);
      imageMs.push(performance.now() - s);
    }

    log("embedding notes");
    const noteVecs: number[][] = [];
    const noteMs: number[] = [];
    for (const n of fx.notes) {
      const s = performance.now();
      noteVecs.push(await miniLm(m, n.text));
      noteMs.push(performance.now() - s);
    }

    // A search embeds one query text with both encoders, so every query text
    // is timed through both. The CLIP vector is kept for image queries and the
    // MiniLM vector for note queries.
    log("embedding queries");
    const queryVecs: number[][] = [];
    const noteQueryVecs: number[][] = [];
    const queryMs: number[] = [];
    const texts = [
      ...fx.image_queries.map((q) => ({ text: q.text, keep: "clip" as const })),
      ...fx.note_queries.map((q) => ({ text: q.text, keep: "minilm" as const })),
    ];
    for (const q of texts) {
      const s = performance.now();
      const clip = await clipText(m, q.text);
      const mini = await miniLm(m, q.text);
      queryMs.push(performance.now() - s);
      if (q.keep === "clip") queryVecs.push(clip);
      else noteQueryVecs.push(mini);
    }

    return {
      timings: {
        load_ms: loadMs,
        image: summarise(imageMs),
        query_both: summarise(queryMs),
        note: summarise(noteMs),
      },
      vectors: { images: imageVecs, image_queries: queryVecs, notes: noteVecs, note_queries: noteQueryVecs },
    };
  } finally {
    for (const model of loaded) await model.dispose().catch(() => undefined);
  }
}

async function run(req: RunRequest): Promise<RunResult> {
  if (req.cold) {
    // Clears the Cache API, where Transformers.js keeps model files. The HTTP
    // cache is out of reach from a page; automated cold runs use a fresh
    // browser profile instead, and the result records network bytes so a
    // cached "cold" run is visible.
    for (const key of await caches.keys()) await caches.delete(key);
  }
  if (onnx.wasm && req.threads) onnx.wasm.numThreads = req.threads;
  env.useBrowserCache = !req.no_cache;

  const webgpu = await gpuInfo();
  let device: Device = req.device;
  let fallback: string | null = null;
  if (device === "webgpu" && !webgpu.adapter) {
    fallback = webgpu.error ?? (webgpu.available ? "no WebGPU adapter" : "navigator.gpu missing");
    device = "wasm";
  }

  const fx: Fixtures = await (await fetch("/fixtures/fixtures.json")).json();
  // Fixture images are fetched before any timer starts, so latency covers
  // decode, preprocessing and inference only, wherever the page is served from.
  const imageBlobs = await Promise.all(fx.images.map(async (i) => (await fetch(`/fixtures/${i.file}`)).blob()));

  let out: Awaited<ReturnType<typeof attempt>>;
  try {
    out = await attempt(device, req, fx, imageBlobs);
  } catch (err) {
    if (device !== "webgpu") throw err;
    fallback = `WebGPU failed: ${String(err)}`;
    log(`falling back to wasm: ${fallback}`);
    device = "wasm";
    out = await attempt(device, req, fx, imageBlobs);
  }

  return {
    label: req.label,
    device,
    requested_device: req.device,
    dtype: req.dtype,
    embed_dtype: req.embed_dtype,
    cold: req.cold,
    no_cache: req.no_cache,
    fallback,
    fixtures_seed: fx.seed,
    env: {
      transformers: env.version,
      onnxruntime_web: onnx.versions?.web ?? null,
      user_agent: navigator.userAgent,
      hardware_concurrency: navigator.hardwareConcurrency,
      cross_origin_isolated: self.crossOriginIsolated,
      wasm_threads: onnx.wasm?.numThreads ?? null,
      webgpu,
    },
    bytes: bytes(),
    ...out,
  };
}

self.onmessage = async (e: MessageEvent<RunRequest>) => {
  try {
    post({ type: "result", result: await run(e.data) });
  } catch (err) {
    post({ type: "error", text: err instanceof Error ? `${err.message}\n${err.stack}` : String(err) });
  }
};
