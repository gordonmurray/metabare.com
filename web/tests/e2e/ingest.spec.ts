import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { addFiles, imagePaths, itemCount, notePaths, ready } from "./fixtures";

test("adds images and notes, and skips them the second time", async ({ page }) => {
    await ready(page);
    const notes = notePaths();
    const summary = await addFiles(page, [...imagePaths, ...notes]);
    expect(summary).toContain(`Added ${imagePaths.length + notes.length}`);
    expect(await itemCount(page)).toBe(imagePaths.length + notes.length);
    await expect(page.locator("#grid li")).toHaveCount(imagePaths.length + notes.length);
    // Thumbnails load lazily, so bring the first into view before checking it decoded.
    const thumb = page.locator("#grid li img").first();
    await thumb.scrollIntoViewIfNeeded();
    await expect
        .poll(() => thumb.evaluate((img: HTMLImageElement) => img.naturalWidth))
        .toBeGreaterThan(0);

    const again = await addFiles(page, imagePaths.slice(0, 5));
    expect(again).toContain("Added 0, 5 already in the library");
});

test("after a reload partway through, choosing the files again skips the finished ones", async ({
    page,
}) => {
    await ready(page);
    await page.locator("#files").setInputFiles(imagePaths);
    // Wait until some, but not all, are stored, then reload mid-run.
    await expect.poll(() => itemCount(page), { timeout: 120_000 }).toBeGreaterThanOrEqual(10);
    await page.reload();
    await expect(page.locator("body[data-ready='1']")).toBeAttached({ timeout: 60_000 });
    const before = await itemCount(page);
    expect(before).toBeGreaterThanOrEqual(10);
    expect(before).toBeLessThan(imagePaths.length);

    const summary = await addFiles(page, imagePaths);
    expect(summary).toContain(
        `Added ${imagePaths.length - before}, ${before} already in the library`,
    );
    expect(await itemCount(page)).toBe(imagePaths.length);
});

test("stops cleanly when storage is full", async ({ page }) => {
    await ready(page, "?simulate-full-after=5");
    const summary = await addFiles(page, imagePaths.slice(0, 12));
    expect(summary).toContain("Added 5");
    expect(summary).toContain("no more storage");
    await expect(page.locator("#summary")).toHaveAttribute("data-stopped", "storage-full");
    expect(await itemCount(page)).toBe(5);
});

test("an exported library imports into a fresh browser profile", async ({ page, browser }) => {
    await ready(page);
    const notes = notePaths().slice(0, 5);
    await addFiles(page, [...imagePaths.slice(0, 5), ...notes]);
    const [download] = await Promise.all([page.waitForEvent("download"), page.click("#export")]);
    const file = await download.path();
    const exported = JSON.parse(readFileSync(file, "utf8"));
    expect(exported.items).toHaveLength(10);

    const fresh = await browser.newContext();
    const other = await fresh.newPage();
    await ready(other);
    expect(await itemCount(other)).toBe(0);
    await other.locator("#import").setInputFiles(file);
    await expect(other.locator("#summary")).toHaveText("Imported 10 items.");
    expect(await itemCount(other)).toBe(10);
    await expect(other.locator("#grid li")).toHaveCount(10);
    await fresh.close();
});
