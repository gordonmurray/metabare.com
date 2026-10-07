// How brute-force search scales with library size: builds an index of random
// unit vectors in the page (?bench hook in main.ts) and times a search, at
// three sizes. Writes results/search-scaling-<browser>.json.

import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { ready } from "./fixtures";

test("search time against library size", async ({ page, browserName }) => {
    await ready(page, "?bench");
    const results = [];
    for (const n of [1_000, 10_000, 50_000]) {
        const r = await page.evaluate(
            (size) =>
                (
                    window as unknown as {
                        metabareBench: (n: number) => {
                            n: number;
                            build_ms: number;
                            search_median_ms: number;
                        };
                    }
                ).metabareBench(size),
            n,
        );
        console.log(browserName, JSON.stringify(r));
        results.push(r);
    }
    const out = resolve(import.meta.dirname, "../../results");
    mkdirSync(out, { recursive: true });
    writeFileSync(
        resolve(out, `search-scaling-${browserName}.json`),
        JSON.stringify(
            {
                browser: browserName,
                user_agent: await page.evaluate(() => navigator.userAgent),
                note: "Half images (512-d), half notes (384-d), random unit vectors; median of 7 searches.",
                results,
            },
            null,
            1,
        ) + "\n",
    );
    expect(results).toHaveLength(3);
});
