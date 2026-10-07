// Runs the models off the main thread. Each model is loaded the first time a
// request needs it, so adding images never loads the text models and a first
// search never loads the image model. Requests are handled one at a time.

import {
    AutoProcessor,
    AutoTokenizer,
    CLIPTextModelWithProjection,
    CLIPVisionModelWithProjection,
    RawImage,
    env,
    pipeline,
    type FeatureExtractionPipeline,
    type PreTrainedTokenizer,
    type Processor,
    type Tensor,
} from "@huggingface/transformers";
import { MODEL_BASE, MODELS, ORT_BASE } from "./config";
import type { Device, WorkerRequest, WorkerResponse } from "./protocol";

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = MODEL_BASE;

// The ONNX Runtime build follows Transformers.js's own default, whose
// detection is not exported: the plain build for Safari before 26 without
// WebGPU, the asyncify build everywhere else.
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
        mjs: `${ORT_BASE}ort-wasm-simd-threaded${ortBuild}.mjs`,
        wasm: `${ORT_BASE}ort-wasm-simd-threaded${ortBuild}.wasm`,
    };
}

const post = (m: WorkerResponse) => self.postMessage(m);

function progress(model: string) {
    return (p: { status: string; file?: string; loaded?: number; total?: number }) => {
        if (p.status === "progress" && p.file)
            post({
                type: "progress",
                model,
                file: p.file,
                loaded: p.loaded ?? 0,
                total: p.total ?? 0,
            });
    };
}

function unit(t: Tensor): Float32Array {
    const f = t.type === "float32" ? t : t.to("float32");
    const data = Float32Array.from(f.data as Float32Array);
    let norm = 0;
    for (const v of data) norm += v * v;
    norm = Math.sqrt(norm);
    for (let i = 0; i < data.length; i++) data[i] = (data[i] ?? 0) / norm;
    return data;
}

let device: Device = "wasm";
let image: Promise<{ processor: Processor; model: CLIPVisionModelWithProjection }> | null = null;
let clipText: Promise<{
    tokenizer: PreTrainedTokenizer;
    model: CLIPTextModelWithProjection;
}> | null = null;
let text: Promise<FeatureExtractionPipeline> | null = null;

async function webgpuUsable(): Promise<boolean> {
    const gpu = (navigator as Navigator & { gpu?: GPU }).gpu;
    if (!gpu) return false;
    try {
        const adapter = await gpu.requestAdapter();
        return adapter !== null;
    } catch {
        return false;
    }
}

// A load that fails is forgotten, so the next request tries again instead of
// repeating the old error.
function retryable<T>(p: Promise<T>, forget: () => void): Promise<T> {
    p.catch(forget);
    return p;
}

function loadImage() {
    image ??= retryable(
        (async () => {
            post({ type: "loading", model: "image" });
            const processor = await AutoProcessor.from_pretrained(MODELS.clip.id);
            const model = await CLIPVisionModelWithProjection.from_pretrained(MODELS.clip.id, {
                device,
                dtype: MODELS.clip.dtype,
                progress_callback: progress("image"),
            });
            post({ type: "loaded", model: "image", device });
            return { processor, model };
        })(),
        () => (image = null),
    );
    return image;
}

function loadClipText() {
    clipText ??= retryable(
        (async () => {
            post({ type: "loading", model: "clip-text" });
            const tokenizer = await AutoTokenizer.from_pretrained(MODELS.clip.id);
            const model = await CLIPTextModelWithProjection.from_pretrained(MODELS.clip.id, {
                device,
                dtype: MODELS.clip.dtype,
                progress_callback: progress("clip-text"),
            });
            post({ type: "loaded", model: "clip-text", device });
            return { tokenizer, model };
        })(),
        () => (clipText = null),
    );
    return clipText;
}

function loadText() {
    text ??= retryable(
        (async () => {
            post({ type: "loading", model: "text" });
            const extractor = await pipeline("feature-extraction", MODELS.text.id, {
                device,
                dtype: MODELS.text.dtype,
                progress_callback: progress("text"),
            });
            post({ type: "loaded", model: "text", device });
            return extractor;
        })(),
        () => (text = null),
    );
    return text;
}

// Releases every loaded session, so changing backend does not leave the old
// models in memory.
async function unload() {
    const loaded = [image, clipText, text];
    image = clipText = text = null;
    for (const p of loaded) {
        const m = await p?.catch(() => null);
        if (!m) continue;
        await ("model" in m ? m.model : m).dispose().catch(() => undefined);
    }
}

async function embedText(value: string): Promise<Float32Array> {
    const extractor = await loadText();
    return unit(await extractor(value, { pooling: "mean", normalize: true }));
}

async function handle(req: WorkerRequest): Promise<WorkerResponse> {
    switch (req.type) {
        case "configure": {
            const wanted = req.device;
            device = wanted === "webgpu" && (await webgpuUsable()) ? "webgpu" : "wasm";
            await unload();
            return { type: "configured", id: req.id, device };
        }
        case "image": {
            const m = await loadImage();
            const inputs = await m.processor(await RawImage.fromBlob(req.blob));
            const { image_embeds } = await m.model(inputs);
            return { type: "vector", id: req.id, vector: unit(image_embeds) };
        }
        case "note":
            return { type: "vector", id: req.id, vector: await embedText(req.text) };
        case "query": {
            const m = await loadClipText();
            const tokens = m.tokenizer([req.text], { padding: true, truncation: true });
            const { text_embeds } = await m.model(tokens);
            return {
                type: "query",
                id: req.id,
                clip: unit(text_embeds),
                text: await embedText(req.text),
            };
        }
    }
}

// One request at a time: ONNX Runtime sessions are not safe to run in
// parallel, and a queue keeps memory predictable.
let chain: Promise<void> = Promise.resolve();
self.onmessage = (e: MessageEvent<WorkerRequest>) => {
    const req = e.data;
    chain = chain.then(async () => {
        try {
            const res = await handle(req);
            self.postMessage(
                res,
                "vector" in res
                    ? [res.vector.buffer]
                    : res.type === "query"
                      ? [res.clip.buffer, res.text.buffer]
                      : [],
            );
        } catch (err) {
            post({
                type: "error",
                id: req.id,
                message: err instanceof Error ? err.message : String(err),
            });
        }
    });
};
