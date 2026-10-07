import { describe, expect, it } from "vitest";
import { VECTOR_SPACE } from "../../src/config";
import type { Item } from "../../src/db";
import { Index, rank, rrf, topK } from "../../src/search";

function unit(...values: number[]): Float32Array {
    const v = Float32Array.from(values);
    const n = Math.hypot(...values);
    return v.map((x) => x / n);
}

function item(id: string, kind: Item["kind"], name: string, text = ""): Item {
    return {
        id,
        kind,
        name,
        path: name,
        type: kind === "image" ? "image/png" : "text/markdown",
        size: 1,
        modified: 0,
        added: 0,
        text,
        excerpt: text,
        space: VECTOR_SPACE,
    };
}

describe("topK", () => {
    it("returns the indices of the highest scores, best first", () => {
        expect(topK(Float32Array.from([0.1, 0.9, 0.5, 0.7]), 3)).toEqual([1, 3, 2]);
    });
});

describe("rank", () => {
    it("orders rows by similarity to the query", () => {
        const matrix = new Float32Array([...unit(1, 0), ...unit(0, 1), ...unit(1, 1)]);
        expect(rank(matrix, ["x", "y", "xy"], unit(1, 0.1))).toEqual(["x", "xy", "y"]);
    });

    it("drops matches below the floor", () => {
        const matrix = new Float32Array([...unit(1, 0), ...unit(0, 1)]);
        expect(rank(matrix, ["x", "y"], unit(1, 0), 0.5)).toEqual(["x"]);
    });

    it("handles an empty matrix", () => {
        expect(rank(new Float32Array(0), [], unit(1, 0))).toEqual([]);
    });
});

describe("rrf", () => {
    it("sums 1 / (k + rank) across lists and records each rank", () => {
        const fused = rrf({ image: ["a", "b"], words: ["b", "c"] }, 60);
        expect(fused.map((f) => f.id)).toEqual(["b", "a", "c"]);
        const b = fused[0]!;
        expect(b.fused).toBeCloseTo(1 / 62 + 1 / 61);
        expect(b.ranks).toEqual({ image: 2, words: 1 });
    });
});

describe("Index", () => {
    const items = [
        item("cat", "image", "IMG_0001.png"),
        item("dog", "image", "IMG_0002.png"),
        item("spot", "note", "spot.md", "The spot node was reclaimed after an interruption."),
        item("pasta", "note", "pasta.md", "Salt the pasta water generously."),
    ];
    const vectors = [
        { id: "cat", image: unit(1, 0, 0) },
        { id: "dog", image: unit(0, 1, 0) },
        { id: "spot", text: unit(1, 0) },
        { id: "pasta", text: unit(0, 1) },
    ];
    const index = new Index(items, vectors);

    it("finds an image by its vector, ahead of notes that only clear no floor", () => {
        const hits = index.search("a cat", { clip: unit(0.9, 0.1, 0), text: unit(0.1, 0.1) }, 24, {
            image: 0.24,
            text: 0.9,
        });
        expect(hits[0]?.item.id).toBe("cat");
        expect(hits[0]?.ranks.image).toBe(1);
    });

    it("puts a note matched by both its vector and its words first", () => {
        const hits = index.search("spot interruption", {
            clip: unit(0, 0, 1),
            text: unit(1, 0.05),
        });
        expect(hits[0]?.item.id).toBe("spot");
        expect(hits[0]?.ranks).toMatchObject({ text: 1, words: 1 });
    });

    it("matches file names as words", () => {
        const hits = index.search("IMG_0002", { clip: unit(0, 0, 1), text: unit(0.5, 0.5) });
        expect(hits.find((h) => h.item.id === "dog")?.ranks.words).toBe(1);
    });
});
