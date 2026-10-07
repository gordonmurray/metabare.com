// Builds public/demo/library.json, the demo library a visitor can load with
// one click: the spike's 50 synthetic images and 30 notes plus the 24
// synthetic technical notes in eval/, embedded by the app itself so the
// vectors match the deployed models. Run when the models or fixtures change:
//
//   BUILD_DEMO=1 npx playwright test demo.build

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { addFiles, imagePaths, notePaths, ready, technicalNotePaths } from "./fixtures";

test("build the demo library", async ({ page }) => {
    test.skip(!process.env.BUILD_DEMO, "set BUILD_DEMO=1 to rebuild the demo library");
    await ready(page);
    const files = [...imagePaths, ...notePaths(), ...technicalNotePaths()];
    expect(await addFiles(page, files)).toContain(`Added ${files.length}`);
    const [download] = await Promise.all([page.waitForEvent("download"), page.click("#export")]);
    const exported = JSON.parse(readFileSync(await download.path(), "utf8"));
    // A fixed date and fixed timestamps, so rebuilding with the same models and
    // fixtures gives the same file.
    exported.exported = "2026-10-07T00:00:00.000Z";
    for (const e of exported.items) {
        e.item.added = 0;
        e.item.modified = 0;
        e.item.path = e.item.name;
    }
    exported.items.sort((a: { item: { name: string } }, b: { item: { name: string } }) =>
        a.item.name.localeCompare(b.item.name),
    );
    const out = resolve(import.meta.dirname, "../../public/demo/library.json");
    mkdirSync(resolve(out, ".."), { recursive: true });
    writeFileSync(out, JSON.stringify(exported) + "\n");
});
