// The page at phone width: nothing wider than the screen, with results shown.

import { expect, test } from "@playwright/test";
import { ready } from "./fixtures";

test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true });

test("fits a phone screen with results shown", async ({ page }) => {
    await ready(page);
    await page.click("#load-demo");
    await expect(page.locator("#search-status")).toContainText("Added 104 demo items");
    await page.fill("#query", "a bar chart");
    await page.click("#search-button");
    await expect(page.locator("#results .card").first()).toBeVisible({ timeout: 120_000 });
    const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    if (process.env.SCREENSHOT) await page.screenshot({ path: process.env.SCREENSHOT });
});
