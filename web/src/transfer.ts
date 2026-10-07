// Export and import of the local library as a single JSON file. The browser
// may clear its storage, and the local library is the only copy, so this is
// how it is kept or moved to another browser.

import { MODELS, VECTOR_SPACE } from "./config";
import type { Item, Library, Vectors } from "./db";

export const FORMAT = "metabare-library";
export const FORMAT_VERSION = 1;

/** Limits on an imported file, checked before anything is allocated or stored. */
export const IMPORT_LIMITS = {
    fileBytes: 512 * 1024 * 1024,
    items: 100_000,
    textChars: 1_000_000,
    thumbBytes: 1024 * 1024,
};

interface ExportedItem {
    item: Item;
    image?: string;
    text?: string;
    thumb?: { type: string; data: string };
}

export interface ExportFile {
    format: typeof FORMAT;
    version: number;
    space: string;
    exported: string;
    items: ExportedItem[];
}

function toBase64(bytes: Uint8Array): string {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000)
        s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(s);
}

function fromBase64(s: string): Uint8Array<ArrayBuffer> {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

export function encodeVector(v: Float32Array): string {
    return toBase64(new Uint8Array(v.buffer, v.byteOffset, v.byteLength));
}

export function decodeVector(s: string): Float32Array {
    const bytes = fromBase64(s);
    return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
}

export async function exportLibrary(library: Library, now = new Date()): Promise<ExportFile> {
    const snap = await library.snapshot();
    const vectors = new Map(snap.vectors.map((v) => [v.id, v]));
    const items: ExportedItem[] = [];
    for (const item of snap.items) {
        const v = vectors.get(item.id);
        const thumb = snap.thumbs.get(item.id);
        items.push({
            item,
            ...(v?.image ? { image: encodeVector(v.image) } : {}),
            ...(v?.text ? { text: encodeVector(v.text) } : {}),
            ...(thumb
                ? {
                      thumb: {
                          type: thumb.type,
                          data: toBase64(new Uint8Array(await thumb.arrayBuffer())),
                      },
                  }
                : {}),
        });
    }
    return {
        format: FORMAT,
        version: FORMAT_VERSION,
        space: VECTOR_SPACE,
        exported: now.toISOString(),
        items,
    };
}

export class ImportError extends Error {}

const isString = (v: unknown): v is string => typeof v === "string";
const isCount = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;

function vector(encoded: unknown, dims: number, what: string): Float32Array {
    if (!isString(encoded) || encoded.length > dims * 8)
        throw new ImportError(`${what} is not a vector`);
    const v = decodeVector(encoded);
    if (v.length !== dims) throw new ImportError(`${what} has ${v.length} dimensions, not ${dims}`);
    if (!v.every(Number.isFinite)) throw new ImportError(`${what} contains a non-finite value`);
    return v;
}

/** Checks one exported item completely and returns what to store. */
function validate(e: unknown): { item: Item; vectors: Vectors; thumb: Blob | null } {
    const x = e as Partial<ExportedItem>;
    const it = x?.item as Partial<Item> | undefined;
    if (!it || !isString(it.id) || !/^[0-9a-f]{64}$/.test(it.id))
        throw new ImportError("an item has no valid id");
    const what = `item ${it.id.slice(0, 12)}`;
    if (it.kind !== "image" && it.kind !== "note") throw new ImportError(`${what} has no kind`);
    for (const key of ["name", "path", "type", "text", "excerpt"] as const)
        if (!isString(it[key])) throw new ImportError(`${what} has no ${key}`);
    for (const key of ["size", "modified", "added"] as const)
        if (!isCount(it[key])) throw new ImportError(`${what} has no ${key}`);
    if (it.space !== VECTOR_SPACE) throw new ImportError(`${what} was made with different models`);
    if ((it.text as string).length > IMPORT_LIMITS.textChars)
        throw new ImportError(`${what} has more text than the limit`);

    const item: Item = {
        id: it.id,
        kind: it.kind,
        name: it.name as string,
        path: it.path as string,
        type: it.type as string,
        size: it.size as number,
        modified: it.modified as number,
        added: it.added as number,
        text: it.text as string,
        excerpt: it.excerpt as string,
        space: it.space,
        ...(isCount(it.width) && isCount(it.height) ? { width: it.width, height: it.height } : {}),
    };
    const vectors: Vectors =
        item.kind === "image"
            ? { id: item.id, image: vector(x.image, MODELS.clip.dims, `${what} image vector`) }
            : { id: item.id, text: vector(x.text, MODELS.text.dims, `${what} text vector`) };

    let thumb: Blob | null = null;
    if (x.thumb !== undefined) {
        const t = x.thumb;
        if (!t || !isString(t.type) || !t.type.startsWith("image/") || !isString(t.data))
            throw new ImportError(`${what} has an invalid thumbnail`);
        if (t.data.length > (IMPORT_LIMITS.thumbBytes * 4) / 3 + 4)
            throw new ImportError(`${what} has a thumbnail over the size limit`);
        thumb = new Blob([fromBase64(t.data)], { type: t.type });
    }
    return { item, vectors, thumb };
}

/**
 * Adds every item in the file that the library does not already have. The
 * whole file is checked before anything is written, so a bad file changes
 * nothing.
 */
export async function importLibrary(library: Library, file: unknown): Promise<number> {
    const f = file as Partial<ExportFile>;
    if (f?.format !== FORMAT || !Array.isArray(f.items))
        throw new ImportError("not a MetaBare library export");
    if (f.version !== FORMAT_VERSION)
        throw new ImportError(`export format version ${f.version} is not supported`);
    if (f.space !== VECTOR_SPACE)
        throw new ImportError(
            "this library was made with different models; its vectors cannot be searched here",
        );
    if (f.items.length > IMPORT_LIMITS.items)
        throw new ImportError(`more than ${IMPORT_LIMITS.items} items`);
    const checked = f.items.map(validate);
    let added = 0;
    for (const c of checked) {
        if (await library.has(c.item.id)) continue;
        await library.put(c.item, c.vectors, c.thumb);
        added++;
    }
    return added;
}
