import { Library, QuotaError } from "./db";
import { Embedder, type LoadEvent } from "./embedder";
import { ingest, type Summary } from "./ingest";
import type { Device } from "./protocol";
import { exportLibrary, IMPORT_LIMITS, importLibrary } from "./transfer";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const GRID_LIMIT = 300;
const GPU_KEY = "metabare.gpu";

const params = new URLSearchParams(location.search);

function readSetting(key: string): string | null {
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

function writeSetting(key: string, value: string) {
    try {
        localStorage.setItem(key, value);
    } catch {
        // Storage blocked: the setting lasts until the page closes.
    }
}

function warn(text: string) {
    const el = $("warning");
    el.textContent = text;
    el.hidden = false;
}

function mb(bytes: number): string {
    return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;
}

async function main() {
    if (!("indexedDB" in window)) {
        warn(
            "This browser does not allow storage for this site, so there is nowhere to keep a library.",
        );
        return;
    }
    if (!crossOriginIsolated)
        warn(
            "This page is not cross-origin isolated, so the models run on one thread and are slower.",
        );

    const library = await Library.open();
    const embedder = new Embedder();

    // Test hook: ?simulate-full-after=N makes storage report full after N items.
    const fullAfter = params.get("simulate-full-after");
    if (fullAfter !== null) {
        let left = Number(fullAfter);
        const put = library.put.bind(library);
        library.put = async (...args) => {
            if (left-- <= 0) throw new QuotaError("storage is full (simulated)");
            return put(...args);
        };
    }

    // Backend.
    const gpu = $<HTMLInputElement>("gpu");
    gpu.checked = readSetting(GPU_KEY) === "1";
    gpu.disabled = !("gpu" in navigator);
    const status = $("status");
    let device: Device = await embedder.configure(gpu.checked ? "webgpu" : "wasm");
    const describe = () =>
        `Models run on ${device === "webgpu" ? "the GPU (WebGPU)" : "the CPU (WebAssembly)"}.` +
        (!("gpu" in navigator) ? " This browser does not offer WebGPU." : "");
    status.textContent = describe();
    gpu.addEventListener("change", async () => {
        writeSetting(GPU_KEY, gpu.checked ? "1" : "0");
        device = await embedder.configure(gpu.checked ? "webgpu" : "wasm");
        if (gpu.checked && device !== "webgpu") gpu.checked = false;
        status.textContent = describe();
    });

    const downloads = new Map<string, { loaded: number; total: number }>();
    embedder.onLoad((e: LoadEvent) => {
        if (e.state === "progress" && e.file) {
            downloads.set(`${e.model}/${e.file}`, { loaded: e.loaded ?? 0, total: e.total ?? 0 });
            let loaded = 0;
            let total = 0;
            for (const d of downloads.values()) {
                loaded += d.loaded;
                total += d.total;
            }
            status.textContent = `Downloading models: ${mb(loaded)} of ${mb(total)}. This happens once.`;
        } else if (e.state === "loaded") {
            status.textContent = describe();
        }
    });

    // Library view.
    const grid = $<HTMLUListElement>("grid");
    let urls: string[] = [];
    async function render() {
        for (const u of urls) URL.revokeObjectURL(u);
        urls = [];
        const items = (await library.items()).sort((a, b) => b.added - a.added);
        $("empty").hidden = items.length > 0;
        const images = items.filter((i) => i.kind === "image").length;
        let stats = `${items.length} items: ${images} images, ${items.length - images} notes.`;
        if (navigator.storage?.estimate) {
            const { usage = 0, quota = 0 } = await navigator.storage.estimate();
            stats += ` Using ${mb(usage)} of ${mb(quota)} available.`;
        }
        $("library-stats").textContent = stats;
        const shown = items.slice(0, GRID_LIMIT);
        const cards = await Promise.all(
            shown.map(async (item) => {
                const li = document.createElement("li");
                li.className = "card";
                li.dataset.id = item.id;
                li.title = item.path;
                if (item.kind === "image") {
                    const img = document.createElement("img");
                    const thumb = await library.thumb(item.id);
                    if (thumb) {
                        const url = URL.createObjectURL(thumb);
                        urls.push(url);
                        img.src = url;
                    }
                    img.alt = item.name;
                    img.loading = "lazy";
                    li.append(img);
                } else {
                    const note = document.createElement("div");
                    note.className = "note";
                    note.textContent = item.excerpt;
                    li.append(note);
                }
                const meta = document.createElement("div");
                meta.className = "meta";
                meta.textContent = item.name;
                li.append(meta);
                return li;
            }),
        );
        grid.replaceChildren(...cards);
        const more = $("more");
        more.hidden = items.length <= GRID_LIMIT;
        more.textContent = `Showing the ${GRID_LIMIT} most recently added of ${items.length}.`;
    }

    // Adding, importing, exporting and clearing all change or read the whole
    // library, so only one runs at a time and the controls for the others are
    // disabled meanwhile.
    let busy = false;
    const busyControls = ["files", "folder", "import", "export", "clear"].map((id) =>
        $<HTMLInputElement | HTMLButtonElement>(id),
    );
    async function exclusive(work: () => Promise<void>): Promise<void> {
        if (busy) return;
        busy = true;
        for (const c of busyControls) c.disabled = true;
        try {
            await work();
        } finally {
            busy = false;
            for (const c of busyControls) c.disabled = false;
        }
    }

    // Adding files.
    let running: AbortController | null = null;
    const progress = $("progress");
    const bar = $<HTMLProgressElement>("progress-bar");
    const progressText = $("progress-text");
    const summaryEl = $("summary");

    function describeSummary(s: Summary): string {
        const parts = [`Added ${s.added}`];
        if (s.skipped) parts.push(`${s.skipped} already in the library`);
        if (s.unsupported) parts.push(`${s.unsupported} not supported`);
        if (s.failed) parts.push(`${s.failed} failed`);
        const end =
            s.stopped === "cancelled"
                ? " Cancelled."
                : s.stopped === "storage-full"
                  ? " Stopped: this browser has no more storage for the site. Remove items or export and clear the library."
                  : "";
        return `${parts.join(", ")}.${end}`;
    }

    async function add(files: File[]) {
        if (files.length === 0) return;
        await exclusive(async () => {
            running = new AbortController();
            // Ask for storage the browser will not clear under pressure. Not
            // awaited: Firefox answers only once the person responds to a
            // prompt, and adding files should not wait for that.
            if (navigator.storage?.persist)
                void navigator.storage
                    .persisted()
                    .then((p) => (p ? true : navigator.storage.persist()))
                    .catch(() => false);
            progress.hidden = false;
            summaryEl.textContent = "";
            bar.max = files.length;
            bar.value = 0;
            let sinceRender = 0;
            const failures: string[] = [];
            try {
                const summary = await ingest(files, {
                    library,
                    embedder,
                    signal: running.signal,
                    onProgress: (p) => {
                        bar.value = p.done;
                        progressText.textContent = `${p.done} of ${p.total}: ${p.file}`;
                        if (p.outcome === "failed") failures.push(`${p.file}: ${p.reason}`);
                        if (p.outcome === "added" && ++sinceRender >= 10) {
                            sinceRender = 0;
                            void render();
                        }
                    },
                });
                summaryEl.textContent = describeSummary(summary);
                summaryEl.dataset.stopped = summary.stopped;
                summaryEl.title = failures.join("\n");
            } finally {
                running = null;
                progress.hidden = true;
                await render();
            }
        });
    }

    $("cancel").addEventListener("click", () => running?.abort());
    for (const id of ["files", "folder"]) {
        const input = $<HTMLInputElement>(id);
        input.addEventListener("change", async () => {
            const files = Array.from(input.files ?? []);
            input.value = "";
            await add(files);
        });
    }
    const drop = $("drop");
    drop.addEventListener("dragover", (e) => {
        e.preventDefault();
        drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
        e.preventDefault();
        drop.classList.remove("over");
        void add(Array.from(e.dataTransfer?.files ?? []));
    });

    // Export, import, clear.
    $("export").addEventListener("click", () =>
        exclusive(async () => {
            const data = await exportLibrary(library);
            const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `metabare-library-${data.exported.slice(0, 10)}.json`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
        }),
    );
    const importInput = $<HTMLInputElement>("import");
    importInput.addEventListener("change", () => {
        const file = importInput.files?.[0];
        importInput.value = "";
        if (!file) return;
        void exclusive(async () => {
            try {
                if (file.size > IMPORT_LIMITS.fileBytes)
                    throw new Error(
                        `the file is larger than ${IMPORT_LIMITS.fileBytes / 1024 / 1024} MB`,
                    );
                const added = await importLibrary(library, JSON.parse(await file.text()));
                summaryEl.textContent = `Imported ${added} items.`;
            } catch (err) {
                summaryEl.textContent = `Import failed: ${err instanceof Error ? err.message : String(err)}`;
            }
            await render();
        });
    });
    $("clear").addEventListener("click", () =>
        exclusive(async () => {
            if (!confirm("Remove every item from this browser's library? This cannot be undone."))
                return;
            await library.clear();
            summaryEl.textContent = "Library cleared.";
            await render();
        }),
    );

    await render();
    document.body.dataset.ready = "1";
}

main().catch((err) =>
    warn(`Something went wrong starting up: ${err instanceof Error ? err.message : err}`),
);
