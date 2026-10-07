// The first-visit memory budget: a fresh browser profile adds the 50 fixture
// images and 30 notes, which loads both the image and the text model, while
// the proportional set size of the browser's whole process tree is sampled
// from /proc. Linux only, and heavy, so it only runs when asked:
//
//   MEMORY_RUNS=5 APP_BROWSERS=chromium,firefox npx playwright test memory
//
// Results go to results/memory/app-<browser>-r<n>.json.

import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { chromium, expect, firefox, test, webkit } from "@playwright/test";
import { imagePaths, notePaths } from "./fixtures";

const runs = Number(process.env.MEMORY_RUNS ?? 0);
/** The plan's first-visit budget, for the whole browser process tree. */
const BUDGET_BYTES = 1.5e9;
const OUT = resolve(import.meta.dirname, "../../results/memory");

function treePss(profile: string): number {
    const parent = new Map<string, string>();
    const roots = new Set<string>();
    for (const pid of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
        try {
            const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
            parent.set(pid, stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1] ?? "0");
            if (readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(profile)) roots.add(pid);
        } catch {
            // Exited between listing and reading.
        }
    }
    const inTree = (pid: string) => {
        for (let p: string | undefined = pid; p && p !== "0"; p = parent.get(p))
            if (roots.has(p)) return true;
        return false;
    };
    let total = 0;
    for (const pid of parent.keys()) {
        if (!inTree(pid)) continue;
        try {
            const m = readFileSync(`/proc/${pid}/smaps_rollup`, "utf8").match(/^Pss:\s+(\d+) kB/m);
            if (m) total += Number(m[1]) * 1024;
        } catch {
            // Exited, or not readable.
        }
    }
    return total;
}

for (let run = 1; run <= runs; run++) {
    test(`first visit memory, run ${run}`, async ({ browserName, baseURL }) => {
        test.skip(process.platform !== "linux", "reads /proc");
        const type = { chromium, firefox, webkit }[browserName];
        const profile = mkdtempSync(join(tmpdir(), "metabare-mem-"));
        const context = await type.launchPersistentContext(profile);
        const samples: { t_ms: number; bytes: number }[] = [];
        const started = Date.now();
        const timer = setInterval(
            () => samples.push({ t_ms: Date.now() - started, bytes: treePss(profile) }),
            500,
        );
        try {
            const page = context.pages()[0] ?? (await context.newPage());
            await page.goto(baseURL ?? "/");
            await expect(page.locator("body[data-ready='1']")).toBeAttached({ timeout: 60_000 });
            const files = [...imagePaths, ...notePaths()];
            await page.locator("#files").setInputFiles(files);
            await expect(page.locator("#summary")).toContainText(`Added ${files.length}`, {
                timeout: 9 * 60_000,
            });
        } finally {
            clearInterval(timer);
            await context.close();
            rmSync(profile, { recursive: true, force: true });
        }
        const max = Math.max(...samples.map((s) => s.bytes));
        mkdirSync(OUT, { recursive: true });
        writeFileSync(
            join(OUT, `app-${browserName}-r${run}.json`),
            JSON.stringify({ browser: browserName, run, max_bytes: max, samples }) + "\n",
        );
        console.log(`${browserName} run ${run}: peak ${(max / 1e6).toFixed(0)} MB`);
        // A browser takes well over 100 MB, so anything less means the
        // sampler did not find its processes.
        expect(samples.length).toBeGreaterThan(4);
        expect(max).toBeGreaterThan(100e6);
        expect(max).toBeLessThanOrEqual(BUDGET_BYTES);
    });
}
