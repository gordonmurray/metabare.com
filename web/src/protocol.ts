// Messages between the page and the inference worker.

export type Device = "webgpu" | "wasm";
export type ModelName = "image" | "clip-text" | "text";

export type WorkerRequest =
    | { type: "configure"; id: number; device: Device }
    | { type: "image"; id: number; blob: Blob }
    | { type: "note"; id: number; text: string }
    | { type: "query"; id: number; text: string };

export type WorkerResponse =
    | { type: "configured"; id: number; device: Device }
    | { type: "vector"; id: number; vector: Float32Array }
    | { type: "query"; id: number; clip: Float32Array; text: Float32Array }
    | { type: "error"; id: number; message: string }
    | { type: "loading"; model: ModelName }
    | { type: "loaded"; model: ModelName; device: Device }
    | { type: "progress"; model: string; file: string; loaded: number; total: number };
