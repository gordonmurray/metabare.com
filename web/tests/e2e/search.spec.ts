// Search through the real page: load the demo library, run every fixture
// query through the search box, and score the results against the fixture
// labels. Writes results/relevance-<browser>-<floors>.json.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { ready } from "./fixtures";

interface Query {
    set: string;
    text: string;
    /** File names of the relevant items. */
    relevant: string[];
}

function queries(): Query[] {
    const fx = JSON.parse(
        readFileSync(
            resolve(import.meta.dirname, "../../../spike/public/fixtures/fixtures.json"),
            "utf8",
        ),
    );
    const evalNotes = JSON.parse(
        readFileSync(resolve(import.meta.dirname, "../../../eval/technical-notes.json"), "utf8"),
    );
    return [
        ...fx.image_queries.map((q: { text: string; relevant: string }) => ({
            set: "images",
            text: q.text,
            relevant: fx.images
                .filter((i: { category: string }) => i.category === q.relevant)
                .map((i: { file: string }) => i.file.split("/").pop()),
        })),
        ...fx.note_queries.map((q: { text: string; relevant: string }) => ({
            set: "notes",
            text: q.text,
            relevant: fx.notes
                .filter((n: { topic: string }) => n.topic === q.relevant)
                .map((n: { id: string }) => `${n.id}.md`),
        })),
        ...evalNotes.queries.map((q: { text: string; relevant: string[] }) => ({
            set: "technical-notes",
            text: q.text,
            relevant: q.relevant.map((id) => `${id}.txt`),
        })),
    ];
}

async function run(page: Page, text: string): Promise<string[]> {
    await page.fill("#query", text);
    const before = await page.locator("#search-status").textContent();
    await page.click("#search-button");
    await expect(page.locator("#search-status")).not.toHaveText(before ?? "", { timeout: 120_000 });
    await expect(page.locator("#search-status")).not.toHaveText("Searching…", {
        timeout: 120_000,
    });
    return page.locator("#results .card .meta").allTextContents();
}

for (const floors of ["on", "none"]) {
    test(`relevance on the demo library, floors ${floors}`, async ({ page, browserName }) => {
        await ready(page, floors === "none" ? "?floors=none" : "");
        await page.click("#load-demo");
        await expect(page.locator("#search-status")).toContainText("Added 104 demo items");

        const scores: Record<string, { recall: number[]; precision10: number[] }> = {};
        const perQuery = [];
        for (const q of queries()) {
            const names = await run(page, q.text);
            const relevant = new Set(q.relevant);
            const recall = names.slice(0, relevant.size).filter((n) => relevant.has(n)).length;
            const p10 = names.slice(0, 10).filter((n) => relevant.has(n)).length;
            const s = (scores[q.set] ??= { recall: [], precision10: [] });
            s.recall.push(recall / relevant.size);
            s.precision10.push(p10 / Math.min(10, relevant.size));
            perQuery.push({ ...q, top10: names.slice(0, 10) });
        }
        const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
        const summary = Object.fromEntries(
            Object.entries(scores).map(([set, s]) => [
                set,
                {
                    queries: s.recall.length,
                    recall_at_r: mean(s.recall),
                    precision_at_10: mean(s.precision10),
                },
            ]),
        );
        console.log(browserName, `floors ${floors}`, JSON.stringify(summary));
        const out = resolve(import.meta.dirname, "../../results");
        mkdirSync(out, { recursive: true });
        writeFileSync(
            resolve(out, `relevance-${browserName}-floors-${floors}.json`),
            JSON.stringify({ browser: browserName, floors, summary, queries: perQuery }, null, 1) +
                "\n",
        );
        // A sanity bound on the shipped configuration, not a target: well below
        // what it scores, so a broken pipeline fails and an ordinary change
        // does not. The run without floors exists only for comparison.
        if (floors === "on")
            for (const s of Object.values(summary)) expect(s.recall_at_r).toBeGreaterThan(0.3);
    });
}
