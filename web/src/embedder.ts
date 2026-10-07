// Page-side client for the inference worker: one promise per request, plus
// model loading events for the status line.

import type { Device, ModelName, WorkerRequest, WorkerResponse } from "./protocol";

type Pending = { resolve: (r: WorkerResponse) => void; reject: (e: Error) => void };

// Distributes Omit over the request union, so each member keeps its own fields.
type NewRequest = WorkerRequest extends infer R
    ? R extends WorkerRequest
        ? Omit<R, "id">
        : never
    : never;

export interface LoadEvent {
    model: ModelName | string;
    state: "loading" | "loaded" | "progress";
    device?: Device;
    file?: string;
    loaded?: number;
    total?: number;
}

export class Embedder {
    private worker: Worker;
    private next = 1;
    private pending = new Map<number, Pending>();
    private listeners = new Set<(e: LoadEvent) => void>();

    constructor() {
        this.worker = new Worker(new URL("./embed.worker.ts", import.meta.url), { type: "module" });
        this.worker.onmessage = (e: MessageEvent<WorkerResponse>) => this.receive(e.data);
        this.worker.onerror = (e) => {
            e.preventDefault();
            const error = new Error(`inference worker failed: ${e.message || "unknown error"}`);
            for (const p of this.pending.values()) p.reject(error);
            this.pending.clear();
        };
    }

    onLoad(listener: (e: LoadEvent) => void): () => void {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    private receive(msg: WorkerResponse) {
        if (msg.type === "loading" || msg.type === "loaded") {
            for (const l of this.listeners)
                l({
                    model: msg.model,
                    state: msg.type,
                    device: "device" in msg ? msg.device : undefined,
                });
            return;
        }
        if (msg.type === "progress") {
            for (const l of this.listeners)
                l({
                    model: msg.model,
                    state: "progress",
                    file: msg.file,
                    loaded: msg.loaded,
                    total: msg.total,
                });
            return;
        }
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        if (msg.type === "error") p.reject(new Error(msg.message));
        else p.resolve(msg);
    }

    private send(req: NewRequest): Promise<WorkerResponse> {
        const id = this.next++;
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            this.worker.postMessage({ ...req, id });
        });
    }

    /** Chooses the backend. WebGPU falls back to WASM when no adapter is available. */
    async configure(device: Device): Promise<Device> {
        const r = await this.send({ type: "configure", device });
        if (r.type !== "configured") throw new Error(`unexpected response ${r.type}`);
        return r.device;
    }

    async image(blob: Blob): Promise<Float32Array> {
        const r = await this.send({ type: "image", blob });
        if (r.type !== "vector") throw new Error(`unexpected response ${r.type}`);
        return r.vector;
    }

    async note(text: string): Promise<Float32Array> {
        const r = await this.send({ type: "note", text });
        if (r.type !== "vector") throw new Error(`unexpected response ${r.type}`);
        return r.vector;
    }

    async query(text: string): Promise<{ clip: Float32Array; text: Float32Array }> {
        const r = await this.send({ type: "query", text });
        if (r.type !== "query") throw new Error(`unexpected response ${r.type}`);
        return { clip: r.clip, text: r.text };
    }
}
