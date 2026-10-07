import { afterEach, describe, expect, it } from "vitest";
import { MODELS, VECTOR_SPACE } from "../../src/config";
import { Library, type Item } from "../../src/db";
import {
    decodeVector,
    encodeVector,
    exportLibrary,
    importLibrary,
    ImportError,
} from "../../src/transfer";

let n = 0;
const open: Library[] = [];
async function fresh(): Promise<Library> {
    const lib = await Library.open(`test-${n++}`);
    open.push(lib);
    return lib;
}
afterEach(() => {
    for (const lib of open.splice(0)) lib.close();
});

const IMG = "a".repeat(64);
const NOTE = "b".repeat(64);

function unitVector(dims: number, hot: number): Float32Array {
    const v = new Float32Array(dims);
    v[hot] = 1;
    return v;
}

function item(id: string, kind: Item["kind"]): Item {
    return {
        id,
        kind,
        name: `${id}.${kind === "image" ? "png" : "md"}`,
        path: id,
        type: kind === "image" ? "image/png" : "text/markdown",
        size: 10,
        modified: 1,
        added: 2,
        text: kind === "note" ? "hello" : "",
        excerpt: kind === "note" ? "hello" : "",
        space: VECTOR_SPACE,
    };
}

describe("vector encoding", () => {
    it("round-trips exactly", () => {
        const v = Float32Array.from([0.1, -0.25, 3.5e-8, 1]);
        expect(Array.from(decodeVector(encodeVector(v)))).toEqual(Array.from(v));
    });
});

describe("export and import", () => {
    it("reproduces the library in a fresh one", async () => {
        const a = await fresh();
        await a.put(
            item(IMG, "image"),
            { id: IMG, image: unitVector(MODELS.clip.dims, 3) },
            new Blob([new Uint8Array([1, 2, 3])], { type: "image/webp" }),
        );
        await a.put(item(NOTE, "note"), { id: NOTE, text: unitVector(MODELS.text.dims, 7) }, null);

        const exported = JSON.parse(JSON.stringify(await exportLibrary(a)));
        const b = await fresh();
        expect(await importLibrary(b, exported)).toBe(2);

        expect((await b.items()).map((i) => i.id).sort()).toEqual([IMG, NOTE]);
        const vectors = new Map((await b.vectors()).map((v) => [v.id, v]));
        expect(Array.from(vectors.get(IMG)?.image ?? [])).toEqual(
            Array.from(unitVector(MODELS.clip.dims, 3)),
        );
        expect(Array.from(vectors.get(NOTE)?.text ?? [])).toEqual(
            Array.from(unitVector(MODELS.text.dims, 7)),
        );
        const thumb = await b.thumb(IMG);
        expect(thumb?.type).toBe("image/webp");
        expect(Array.from(new Uint8Array(await thumb!.arrayBuffer()))).toEqual([1, 2, 3]);

        // Importing again adds nothing.
        expect(await importLibrary(b, exported)).toBe(0);
    });

    it("refuses a file from a different vector space", async () => {
        const lib = await fresh();
        const file = { format: "metabare-library", version: 1, space: "other", items: [] };
        await expect(importLibrary(lib, file)).rejects.toThrow(ImportError);
    });

    it("refuses the whole file when any item is invalid, and stores nothing", async () => {
        const a = await fresh();
        await a.put(item(IMG, "image"), { id: IMG, image: unitVector(MODELS.clip.dims, 0) }, null);
        const good = JSON.parse(JSON.stringify(await exportLibrary(a)));
        const cases: [string, (f: typeof good) => void][] = [
            ["missing fields", (f) => (f.items[0].item = { id: IMG })],
            ["bad id", (f) => (f.items[0].item.id = "abc")],
            ["wrong dimensions", (f) => (f.items[0].image = encodeVector(new Float32Array(3)))],
            ["missing vector", (f) => delete f.items[0].image],
            [
                "non-finite value",
                (f) =>
                    (f.items[0].image = encodeVector(
                        new Float32Array(MODELS.clip.dims).fill(Number.NaN),
                    )),
            ],
            ["other item space", (f) => (f.items[0].item.space = "other")],
            ["bad thumbnail type", (f) => (f.items[0].thumb = { type: "text/html", data: "" })],
        ];
        for (const [name, mutate] of cases) {
            const f = structuredClone(good);
            mutate(f);
            const b = await fresh();
            await expect(importLibrary(b, f), name).rejects.toThrow(ImportError);
            expect(await b.count(), name).toBe(0);
        }
    });

    it("refuses something that is not an export", async () => {
        const lib = await fresh();
        await expect(importLibrary(lib, { hello: "world" })).rejects.toThrow(ImportError);
    });
});
