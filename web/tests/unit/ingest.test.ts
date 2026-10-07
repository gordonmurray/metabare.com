import { describe, expect, it } from "vitest";
import { excerpt, kindOf, sha256 } from "../../src/ingest";

describe("sha256", () => {
    it("hashes bytes to lowercase hex", async () => {
        expect(await sha256(new Blob(["abc"]))).toBe(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        );
    });
});

describe("kindOf", () => {
    it("accepts the supported image types", () => {
        for (const type of ["image/png", "image/jpeg", "image/webp"])
            expect(kindOf(new File([], "x", { type }))).toBe("image");
    });

    it("recognises notes by extension, whatever the reported type", () => {
        expect(kindOf(new File([], "Notes.MD"))).toBe("note");
        expect(kindOf(new File([], "a.txt", { type: "text/plain" }))).toBe("note");
        expect(kindOf(new File([], "b.markdown"))).toBe("note");
    });

    it("rejects everything else", () => {
        expect(kindOf(new File([], "a.gif", { type: "image/gif" }))).toBeNull();
        expect(kindOf(new File([], "a.pdf", { type: "application/pdf" }))).toBeNull();
    });
});

describe("excerpt", () => {
    it("drops heading markers and joins lines", () => {
        expect(excerpt("# Title\r\n\nFirst line\n  second  ")).toBe("Title · First line · second");
    });

    it("truncates to the limit with an ellipsis", () => {
        const out = excerpt("x".repeat(500), 10);
        expect(out).toHaveLength(10);
        expect(out.endsWith("…")).toBe(true);
    });
});
