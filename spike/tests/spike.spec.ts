// Drives the spike page through a matrix of settings and writes each result to
// results/<label>/.
//
//   SPIKE_RUNS   comma-separated device:dtype[:embed_dtype], default wasm:q4f16:q8
//   SPIKE_LABEL  names the machine, default "ci"
//   SPIKE_BROWSER chromium (default), firefox or webkit, as built by Playwright
//   SPIKE_CHANNEL chrome, to use the installed Google Chrome instead
//
// Each entry runs twice in one persistent browser profile: first with the
// cache cleared, then again so the models load from the Cache API. A fresh
// Playwright context per test would make every run a cold one, and its
// storage quota is too small to cache the larger model files.

import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium, expect, firefox, test, webkit, type Page } from "@playwright/test";
import { runName, type MemoryReport, type RunResult } from "../src/types";

const label = process.env.SPIKE_LABEL ?? "ci";
const channel = process.env.SPIKE_CHANNEL;
const browserName = process.env.SPIKE_BROWSER ?? "chromium";
const browserType = { chromium, firefox, webkit }[browserName];
if (!browserType) throw new Error(`unknown SPIKE_BROWSER ${browserName}`);
// Result files start with the browser, so one machine's runs sit together.
const prefix = channel ?? browserName;
const runs = (process.env.SPIKE_RUNS ?? "wasm:q4f16:q8").split(",").map((r) => {
  const [device, dtype, embed] = r.split(":");
  return { device, dtype, embed_dtype: embed ?? dtype };
});

// Proportional set size, summed over the browser's whole process tree: every
// process whose command line names this run's profile directory, plus all of
// their descendants (renderer, GPU and utility processes), found through the
// parent pid in /proc/<pid>/stat. PSS splits shared pages between processes,
// so the sum does not double count. Linux only; elsewhere this returns null.
function profilePss(profile: string): number | null {
  if (process.platform !== "linux") return null;
  const parent = new Map<string, string>();
  const roots = new Set<string>();
  for (const pid of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      parent.set(pid, stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1]);
      if (readFileSync(`/proc/${pid}/cmdline`, "utf8").includes(profile)) roots.add(pid);
    } catch {
      // The process exited between listing and reading.
    }
  }
  const inTree = (pid: string): boolean => {
    for (let p: string | undefined = pid; p && p !== "0"; p = parent.get(p)) if (roots.has(p)) return true;
    return false;
  };
  let total = 0;
  for (const pid of parent.keys()) {
    if (!inTree(pid)) continue;
    try {
      const pss = readFileSync(`/proc/${pid}/smaps_rollup`, "utf8").match(/^Pss:\s+(\d+) kB/m);
      if (pss) total += Number(pss[1]) * 1024;
    } catch {
      // Exited, or not readable.
    }
  }
  return total;
}

async function measure(page: Page, qs: URLSearchParams, profile: string): Promise<RunResult> {
  const samples: MemoryReport["samples"] = [];
  const started = Date.now();
  const timer = setInterval(() => {
    const bytes = profilePss(profile);
    if (bytes !== null) samples.push({ t_ms: Date.now() - started, bytes });
  }, 500);
  try {
    await page.goto(`http://localhost:5173/?${qs}`);
    await page.waitForFunction(() => window.spikeResult || window.spikeError, null, {
      timeout: 25 * 60 * 1000,
      polling: 1000,
    });
  } finally {
    clearInterval(timer);
  }
  const error = await page.evaluate(() => window.spikeError);
  expect(error, error).toBeUndefined();
  const result = await page.evaluate(() => window.spikeResult!);
  result.process_memory = {
    method: samples.length ? "linux-pss-of-profile-processes" : "unavailable",
    max_bytes: samples.length ? Math.max(...samples.map((x) => x.bytes)) : null,
    samples,
  };
  return result;
}

for (const run of runs) {
  test(`${run.device} ${run.dtype} embed ${run.embed_dtype}`, async () => {
    const profile = mkdtempSync(join(tmpdir(), "spike-"));
    const context = await browserType.launchPersistentContext(profile, {
      ...(channel ? { channel } : {}),
      // Lets headless Chromium reach the GPU on Linux. Other browsers take
      // their defaults.
      ...(browserName === "chromium"
        ? { args: ["--enable-unsafe-webgpu", "--enable-features=Vulkan", "--use-angle=vulkan"] }
        : {}),
    });
    try {
      const page = context.pages()[0] ?? (await context.newPage());
      page.on("response", (r) => {
        if (r.status() >= 400) console.log(`http ${r.status()} ${r.url()}`);
      });
      page.on("console", (m) => {
        if (m.type() === "error" || m.type() === "warning") console.log(`console: ${m.text()}`);
      });

      const dir = `results/${label}`;
      mkdirSync(dir, { recursive: true });
      for (const cold of [true, false]) {
        const qs = new URLSearchParams({ ...run, label, auto: "1", ...(cold ? { cold: "1" } : {}) });
        const result = await measure(page, qs, profile);
        expect(result.vectors.images).toHaveLength(50);
        expect(result.env.cross_origin_isolated).toBe(true);
        // Named for the device requested, so a WebGPU run that fell back to
        // WASM keeps its own file instead of overwriting the WASM run.
        const name = `${prefix}-${runName({ ...result, device: result.requested_device })}`;
        writeFileSync(`${dir}/${name}.json`, JSON.stringify(result) + "\n");
        console.log(
          `${name}: load ${result.timings.load_ms.toFixed(0)} ms, ` +
            `${(result.bytes.models_network / 1e6).toFixed(1)} MB models over the network, image median ` +
            `${result.timings.image.median_rest_ms.toFixed(0)} ms, fallback ${result.fallback ?? "none"}`,
        );
      }
    } finally {
      await context.close();
      rmSync(profile, { recursive: true, force: true });
    }
  });
}
