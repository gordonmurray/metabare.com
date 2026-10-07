// Search over the local library, entirely in the browser.
//
// Three rankings, fused by Reciprocal Rank Fusion:
//   1. images, by cosine similarity of their CLIP vector to the query's CLIP
//      text vector;
//   2. notes, by cosine similarity of their MiniLM vector to the query's;
//   3. every item, by BM25 over note text and file names.
// RRF uses only each item's rank in each list, so the three kinds of score
// never have to be put on one scale. The fused number orders results and has
// no meaning of its own, so it is not shown.

import MiniSearch from "minisearch";
import type { Item, Vectors } from "./db";

/** Candidates taken from each ranking before fusion. */
export const CANDIDATES = 50;
/** The usual RRF constant: it damps the weight of the very top ranks. */
export const RRF_K = 60;

/**
 * Lowest cosine similarity a vector match needs to enter fusion. Images and
 * notes are ranked separately, so without a floor the best note would tie
 * the best image for any query, however unrelated. Provisional values, from
 * the spike fixtures: CLIP query-to-image similarities for relevant pairs
 * were 0.267 at the 10th percentile, irrelevant 0.260 at the 90th; MiniLM
 * relevant 0.083 at the 10th, irrelevant 0.198 at the 90th. To be tuned on a
 * real evaluation set.
 */
export const FLOORS = { image: 0.24, text: 0.2 };

export type Stage = "image" | "text" | "words";

export interface Hit {
    item: Item;
    /** The rank this item had in each ranking that included it, from 1. */
    ranks: Partial<Record<Stage, number>>;
    fused: number;
}

/** Vectors are unit length, so the dot product is the cosine similarity. */
export function dot(a: Float32Array, b: Float32Array): number {
    let s = 0;
    for (let i = 0; i < a.length; i++) s += (a[i] ?? 0) * (b[i] ?? 0);
    return s;
}

/** Indices of the `k` highest scores, best first. */
export function topK(scores: Float32Array, k: number): number[] {
    const order = Array.from(scores.keys());
    order.sort((x, y) => (scores[y] ?? 0) - (scores[x] ?? 0));
    return order.slice(0, k);
}

/** Fuses ranked id lists: each id scores the sum of 1 / (k + rank). */
export function rrf(lists: Partial<Record<Stage, string[]>>, k = RRF_K) {
    const fused = new Map<string, { fused: number; ranks: Partial<Record<Stage, number>> }>();
    for (const [stage, ids] of Object.entries(lists) as [Stage, string[]][]) {
        ids.forEach((id, i) => {
            const entry = fused.get(id) ?? { fused: 0, ranks: {} };
            entry.fused += 1 / (k + i + 1);
            entry.ranks[stage] = i + 1;
            fused.set(id, entry);
        });
    }
    return [...fused.entries()].map(([id, e]) => ({ id, ...e })).sort((a, b) => b.fused - a.fused);
}

/** The library held in memory for searching: flat vector matrices and a word index. */
export class Index {
    private items = new Map<string, Item>();
    private imageIds: string[] = [];
    private imageMatrix: Float32Array = new Float32Array(0);
    private textIds: string[] = [];
    private textMatrix: Float32Array = new Float32Array(0);
    private words = new MiniSearch<Item>({
        fields: ["name", "text"],
        storeFields: [],
        searchOptions: { prefix: true, fuzzy: 0.15, boost: { text: 2 } },
    });

    constructor(items: Item[], vectors: Vectors[]) {
        const byId = new Map(vectors.map((v) => [v.id, v]));
        const images: Float32Array[] = [];
        const texts: Float32Array[] = [];
        for (const item of items) {
            this.items.set(item.id, item);
            const v = byId.get(item.id);
            if (v?.image) {
                this.imageIds.push(item.id);
                images.push(v.image);
            }
            if (v?.text) {
                this.textIds.push(item.id);
                texts.push(v.text);
            }
        }
        this.imageMatrix = flatten(images);
        this.textMatrix = flatten(texts);
        this.words.addAll(items);
    }

    get size(): number {
        return this.items.size;
    }

    search(
        query: string,
        vectors: { clip: Float32Array; text: Float32Array },
        limit = 24,
        floors = FLOORS,
    ): Hit[] {
        const lists: Partial<Record<Stage, string[]>> = {
            image: rank(this.imageMatrix, this.imageIds, vectors.clip, floors.image),
            text: rank(this.textMatrix, this.textIds, vectors.text, floors.text),
            words: this.words
                .search(query)
                .slice(0, CANDIDATES)
                .map((r) => String(r.id)),
        };
        return rrf(lists)
            .slice(0, limit)
            .flatMap(({ id, fused, ranks }) => {
                const item = this.items.get(id);
                return item ? [{ item, fused, ranks }] : [];
            });
    }
}

function flatten(rows: Float32Array[]): Float32Array {
    const dims = rows[0]?.length ?? 0;
    const out = new Float32Array(rows.length * dims);
    rows.forEach((r, i) => out.set(r, i * dims));
    return out;
}

/** Brute-force nearest neighbours over a flat row-major matrix, above a floor. */
export function rank(
    matrix: Float32Array,
    ids: string[],
    query: Float32Array,
    floor = -1,
): string[] {
    if (ids.length === 0) return [];
    const dims = query.length;
    const scores = new Float32Array(ids.length);
    for (let r = 0; r < ids.length; r++) {
        let s = 0;
        const base = r * dims;
        for (let i = 0; i < dims; i++) s += (matrix[base + i] ?? 0) * (query[i] ?? 0);
        scores[r] = s;
    }
    return topK(scores, CANDIDATES)
        .filter((i) => (scores[i] ?? -1) >= floor)
        .map((i) => ids[i] ?? "");
}
