// Settings come from the query string so the same page serves a person
// clicking through it and Playwright driving it:
//   ?device=webgpu|wasm &dtype=fp32|fp16|q8|q4f16 &embed_dtype=<same choices>
//   &cold=1 &threads=N &no_cache=1 &label=x &auto=1

import {
  runName,
  type Device,
  type Dtype,
  type MemoryReport,
  type RunRequest,
  type RunResult,
  type WorkerMessage,
} from "./types";

declare global {
  interface Window {
    spikeResult?: RunResult;
    spikeError?: string;
  }
}

// A run that has not finished by then is reported as failed rather than left
// waiting, so a hung worker cannot stall an automated matrix.
const WATCHDOG_MS = 20 * 60 * 1000;

const params = new URLSearchParams(location.search);
const req: RunRequest = {
  label: params.get("label") ?? "manual",
  device: (params.get("device") as Device) ?? "webgpu",
  dtype: (params.get("dtype") as Dtype) ?? "q4f16",
  embed_dtype: (params.get("embed_dtype") as Dtype) ?? (params.get("dtype") as Dtype) ?? "q8",
  cold: params.get("cold") === "1",
  threads: params.has("threads") ? Number(params.get("threads")) : null,
  no_cache: params.get("no_cache") === "1",
};

const out = document.querySelector<HTMLPreElement>("#log")!;
const form = document.querySelector<HTMLFormElement>("#settings")!;
const say = (line: string) => {
  out.textContent += `${line}\n`;
};

for (const [k, v] of Object.entries(req)) {
  const field = form.elements.namedItem(k);
  if (field instanceof HTMLInputElement && field.type === "checkbox") field.checked = Boolean(v);
  else if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement)
    field.value = v === null ? "" : String(v);
}

type MemoryApi = Performance & { measureUserAgentSpecificMemory?: () => Promise<{ bytes: number }> };

// measureUserAgentSpecificMemory exists only in cross-origin isolated Chromium
// pages and counts the page's workers too. Chromium may hold each result until
// a garbage collection, so a request is started every second without waiting
// for the last one, each result is kept with the time it was requested, and
// stopping waits a bounded time for outstanding requests. Results that arrive
// later are counted as dropped rather than silently changing the report.
function memorySampler() {
  const api = performance as MemoryApi;
  const available = crossOriginIsolated && typeof api.measureUserAgentSpecificMemory === "function";
  const started = performance.now();
  const samples: MemoryReport["samples"] = [];
  const pending = new Set<Promise<void>>();
  let open = true;
  const request = () => {
    const t = performance.now() - started;
    const p: Promise<void> = api
      .measureUserAgentSpecificMemory!()
      .then(({ bytes }) => {
        if (open) samples.push({ t_ms: t, bytes });
      })
      .catch(() => undefined)
      .finally(() => pending.delete(p));
    pending.add(p);
  };
  const timer = available ? setInterval(request, 1000) : undefined;
  if (available) request();
  return async (): Promise<MemoryReport> => {
    clearInterval(timer);
    await Promise.race([Promise.all(pending), new Promise((r) => setTimeout(r, 15000))]);
    open = false;
    samples.sort((a, b) => a.t_ms - b.t_ms);
    return {
      method: available ? "measureUserAgentSpecificMemory" : "unavailable",
      max_bytes: samples.length ? Math.max(...samples.map((s) => s.bytes)) : null,
      samples,
      dropped: pending.size,
    };
  };
}

function download(result: RunResult) {
  const blob = new Blob([JSON.stringify(result)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${result.label}-${runName(result)}.json`;
  a.textContent = `download ${a.download}`;
  document.body.append(a);
}

function report(r: RunResult) {
  const t = r.timings;
  const mb = (n: number) => `${(n / 1e6).toFixed(1)} MB`;
  say(`device=${r.device} fallback=${r.fallback ?? "none"} threads=${r.env.wasm_threads}`);
  say(`webgpu adapter: ${r.env.webgpu.adapter ?? "none"} shader-f16=${r.env.webgpu.shader_f16}`);
  say(`models: ${mb(r.bytes.models_network)} over the network, ${mb(r.bytes.models_payload)} payload`);
  say(`load ${t.load_ms.toFixed(0)} ms`);
  say(`image first ${t.image.first_ms.toFixed(0)} ms, median ${t.image.median_rest_ms.toFixed(0)} ms`);
  say(`query (both encoders) first ${t.query_both.first_ms.toFixed(0)} ms, median ${t.query_both.median_rest_ms.toFixed(0)} ms`);
  say(`note first ${t.note.first_ms.toFixed(0)} ms, median ${t.note.median_rest_ms.toFixed(0)} ms`);
  const m = r.memory;
  say(`memory ${m?.max_bytes ? `${mb(m.max_bytes)} max of ${m.samples.length} samples` : "not measurable here"}`);
}

function start() {
  say(`crossOriginIsolated=${crossOriginIsolated} navigator.gpu=${"gpu" in navigator}`);
  say(`run ${JSON.stringify(req)}`);
  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  const stopMemory = memorySampler();
  let done = false;

  const fail = async (text: string) => {
    if (done) return;
    done = true;
    clearTimeout(watchdog);
    worker.terminate();
    await stopMemory();
    say(`error: ${text}`);
    window.spikeError = text;
  };
  const watchdog = setTimeout(() => fail(`no result after ${WATCHDOG_MS / 60000} minutes`), WATCHDOG_MS);

  worker.onerror = (e) => {
    e.preventDefault();
    fail(`worker failed to start or crashed: ${e.message || "unknown error"}`);
  };
  worker.onmessageerror = () => fail("worker sent a message that could not be read");
  worker.onmessage = async (e: MessageEvent<WorkerMessage>) => {
    const msg = e.data;
    if (msg.type === "progress") return say(msg.text);
    if (msg.type === "error") return fail(msg.text);
    if (done) return;
    done = true;
    clearTimeout(watchdog);
    const r = msg.result;
    r.memory = await stopMemory();
    worker.terminate();
    report(r);
    window.spikeResult = r;
    download(r);
  };
  worker.postMessage(req);
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const data = new FormData(form);
  const next = new URLSearchParams();
  for (const [k, v] of data) if (v !== "") next.set(k, String(v));
  next.set("auto", "1");
  location.search = next.toString();
});

if (params.get("auto") === "1") start();
