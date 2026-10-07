export type Device = "webgpu" | "wasm";
export type Dtype = "fp32" | "fp16" | "q8" | "q4f16";

export interface Fixtures {
  seed: number;
  images: { file: string; category: string }[];
  notes: { id: string; topic: string; text: string }[];
  image_queries: { text: string; relevant: string }[];
  note_queries: { text: string; relevant: string }[];
}

export interface RunRequest {
  label: string;
  device: Device;
  dtype: Dtype;
  /** Precision for the MiniLM text embedder; the CLIP models use dtype. */
  embed_dtype: Dtype;
  cold: boolean;
  threads: number | null;
  /** Skip the Cache API entirely, for the memory investigation. */
  no_cache: boolean;
}

export interface Timing {
  first_ms: number;
  median_rest_ms: number;
  samples_ms: number[];
}

export interface MemoryReport {
  /** "measureUserAgentSpecificMemory" or "unavailable". */
  method: string;
  /** Largest sample taken while the worker was running, in bytes. */
  max_bytes: number | null;
  /** Milliseconds since the run started, and bytes, for each sample. */
  samples: { t_ms: number; bytes: number }[];
  /** Requests still unanswered when the report was taken. */
  dropped?: number;
}

export interface RunResult {
  label: string;
  device: Device;
  requested_device: Device;
  dtype: Dtype;
  embed_dtype: Dtype;
  cold: boolean;
  no_cache?: boolean;
  fallback: string | null;
  fixtures_seed: number;
  env: {
    transformers: string;
    onnxruntime_web: string | null;
    user_agent: string;
    hardware_concurrency: number;
    cross_origin_isolated: boolean;
    wasm_threads: number | null;
    webgpu: { available: boolean; adapter: string | null; shader_f16: boolean; error: string | null };
  };
  bytes: {
    models_network: number;
    models_payload: number;
    runtime_network: number;
    runtime_payload: number;
  };
  timings: {
    load_ms: number;
    image: Timing;
    /** One query through both the CLIP text encoder and the MiniLM embedder. */
    query_both: Timing;
    note: Timing;
  };
  /** In-page measurement, Chromium only. */
  memory?: MemoryReport;
  /**
   * Proportional set size of every browser process using the run's profile,
   * GPU process included, sampled from /proc by the test driver. Linux only.
   */
  process_memory?: MemoryReport;
  vectors: {
    images: number[][];
    image_queries: number[][];
    notes: number[][];
    note_queries: number[][];
  };
}

export type WorkerMessage =
  | { type: "progress"; text: string }
  | { type: "result"; result: RunResult }
  | { type: "error"; text: string };

export function runName(r: {
  device: string;
  dtype: string;
  embed_dtype: string;
  cold: boolean;
  no_cache?: boolean;
}) {
  const embed = r.embed_dtype === r.dtype ? "" : `-embed-${r.embed_dtype}`;
  return `${r.device}-${r.dtype}${embed}${r.no_cache ? "-nocache" : ""}${r.cold ? "-cold" : ""}`;
}
