// Adding files to the local library: hash, skip what is already stored,
// thumbnail, embed, store. One file at a time, so memory stays flat however
// many files are dropped in.

import { IMAGE_TYPES, LIMITS, NOTE_EXTENSIONS, VECTOR_SPACE } from "./config";
import { QuotaError, type Item, type Library, type Vectors } from "./db";
import type { Embedder } from "./embedder";

export type Outcome = "added" | "skipped" | "unsupported" | "failed";

export interface Progress {
    done: number;
    total: number;
    file: string;
    outcome: Outcome;
    reason?: string;
}

export interface Summary {
    added: number;
    skipped: number;
    unsupported: number;
    failed: number;
    stopped: "finished" | "cancelled" | "storage-full";
}

export async function sha256(blob: Blob): Promise<string> {
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function kindOf(file: File): Item["kind"] | null {
    if (IMAGE_TYPES.includes(file.type)) return "image";
    const name = file.name.toLowerCase();
    if (NOTE_EXTENSIONS.some((ext) => name.endsWith(ext))) return "note";
    return null;
}

/** A note's excerpt: its first heading or line, then the start of the body. */
export function excerpt(text: string, limit = LIMITS.excerptChars): string {
    const flat = text
        .replace(/\r\n?/g, "\n")
        .split("\n")
        .map((l) => l.replace(/^#+\s*/, "").trim())
        .filter(Boolean)
        .join(" · ");
    return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

/**
 * The image's size from its header. An <img> learns its natural size when it
 * loads, without decoding every pixel, so an oversized image is refused
 * before the full decode allocates memory for it.
 */
function imageSize(file: File): Promise<{ width: number; height: number }> {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            URL.revokeObjectURL(url);
            resolve({ width: img.naturalWidth, height: img.naturalHeight });
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("not a readable image"));
        };
        img.src = url;
    });
}

async function thumbnail(file: File): Promise<{ blob: Blob; width: number; height: number }> {
    const size = await imageSize(file);
    if (size.width * size.height > LIMITS.maxImagePixels)
        throw new Error(
            `image is ${size.width}×${size.height}, larger than ${LIMITS.maxImagePixels} pixels`,
        );
    const bitmap = await createImageBitmap(file);
    try {
        const { width, height } = bitmap;
        const scale = Math.min(1, LIMITS.thumbnailSize / Math.max(width, height));
        const w = Math.max(1, Math.round(width * scale));
        const h = Math.max(1, Math.round(height * scale));
        const canvas = new OffscreenCanvas(w, h);
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("no 2D canvas context");
        ctx.drawImage(bitmap, 0, 0, w, h);
        return {
            blob: await canvas.convertToBlob({ type: "image/webp", quality: 0.8 }),
            width,
            height,
        };
    } finally {
        bitmap.close();
    }
}

export interface IngestOptions {
    library: Library;
    embedder: Embedder;
    onProgress?: (p: Progress) => void;
    signal?: AbortSignal;
}

export async function ingest(files: File[], opts: IngestOptions): Promise<Summary> {
    const summary: Summary = {
        added: 0,
        skipped: 0,
        unsupported: 0,
        failed: 0,
        stopped: "finished",
    };
    let done = 0;
    const report = (file: File, outcome: Outcome, reason?: string) => {
        summary[outcome]++;
        done++;
        opts.onProgress?.({ done, total: files.length, file: file.name, outcome, reason });
    };

    for (const file of files) {
        if (opts.signal?.aborted) {
            summary.stopped = "cancelled";
            break;
        }
        const kind = kindOf(file);
        if (!kind) {
            report(file, "unsupported", file.type || "unknown type");
            continue;
        }
        if (file.size > LIMITS.maxFileBytes) {
            report(file, "unsupported", `larger than ${LIMITS.maxFileBytes / 1024 / 1024} MB`);
            continue;
        }
        try {
            const id = await sha256(file);
            if (await opts.library.has(id)) {
                report(file, "skipped", "already in the library");
                continue;
            }
            const base: Item = {
                id,
                kind,
                name: file.name,
                path: file.webkitRelativePath || file.name,
                type: file.type || (kind === "note" ? "text/plain" : ""),
                size: file.size,
                modified: file.lastModified,
                added: Date.now(),
                text: "",
                excerpt: "",
                space: VECTOR_SPACE,
            };
            if (kind === "image") {
                const thumb = await thumbnail(file);
                const vector = await opts.embedder.image(file);
                const item: Item = { ...base, width: thumb.width, height: thumb.height };
                const vectors: Vectors = { id, image: vector };
                await opts.library.put(item, vectors, thumb.blob);
            } else {
                const text = await file.text();
                const vector = await opts.embedder.note(text);
                const item: Item = { ...base, text, excerpt: excerpt(text) };
                await opts.library.put(item, { id, text: vector }, null);
            }
            report(file, "added");
        } catch (err) {
            if (err instanceof QuotaError) {
                report(file, "failed", "storage is full");
                summary.stopped = "storage-full";
                break;
            }
            report(file, "failed", err instanceof Error ? err.message : String(err));
        }
    }
    return summary;
}
