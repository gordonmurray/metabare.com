// Test inputs: the spike's 50 synthetic images, and its 30 notes written out
// as Markdown files.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, type Page } from "@playwright/test";

const SPIKE = resolve(import.meta.dirname, "../../../spike/public/fixtures");

interface Fixtures {
    images: { file: string }[];
    notes: { id: string; text: string }[];
}

const fx: Fixtures = JSON.parse(readFileSync(join(SPIKE, "fixtures.json"), "utf8"));

export const imagePaths = fx.images.map((i) => join(SPIKE, i.file));

export function notePaths(): string[] {
    const dir = join(tmpdir(), "metabare-e2e-notes");
    mkdirSync(dir, { recursive: true });
    return fx.notes.map((n) => {
        const path = join(dir, `${n.id}.md`);
        writeFileSync(path, `# ${n.id}\n\n${n.text}\n`);
        return path;
    });
}

export async function ready(page: Page, query = "") {
    await page.goto(`/${query}`);
    await expect(page.locator("body[data-ready='1']")).toBeAttached({ timeout: 60_000 });
}

/** Chooses files and waits until the run reports its summary. */
export async function addFiles(page: Page, paths: string[]) {
    await page.locator("#files").setInputFiles(paths);
    await expect(page.locator("#summary")).not.toHaveText("", { timeout: 9 * 60_000 });
    return page.locator("#summary").textContent();
}

export async function itemCount(page: Page): Promise<number> {
    return page.evaluate(
        () =>
            new Promise<number>((resolve, reject) => {
                const open = indexedDB.open("metabare");
                open.onerror = () => reject(open.error);
                open.onsuccess = () => {
                    const r = open.result.transaction("items").objectStore("items").count();
                    r.onsuccess = () => {
                        open.result.close();
                        resolve(r.result);
                    };
                };
            }),
    );
}
