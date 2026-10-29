import { expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { test } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

function rows(database: string, query: string) {
  return JSON.parse(execFileSync('python', ['-c', 'import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); c.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in c.execute(sys.argv[2])]))', database, query], { encoding: 'utf8' }));
}

test('a committed impression whose response is lost retries the same event once', async ({ page, gallery }) => {
  let first: { event_id: string; listing_id: string } | undefined;
  const retried: string[] = [];
  await page.route('**/api/signals', async route => {
    const event = route.request().postDataJSON().events[0];
    if (event.kind !== 'impression') return route.continue();
    if (!first) {
      first = { event_id: event.event_id, listing_id: event.listing_id };
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      expect(await response.json()).toEqual({ recorded: 1 });
      await route.abort();
      return;
    }
    if (event.listing_id !== first.listing_id) return route.continue();
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    expect(await response.json()).toEqual({ recorded: 0 });
    await route.fulfill({ response });
    retried.push(event.event_id);
  });
  await page.goto(gallery.url);
  await page.getByRole('article').first().scrollIntoViewIfNeeded();
  await expect.poll(() => first).toBeDefined();
  await expect.poll(() => retried.length, { timeout: 15000 }).toBe(1);
  expect(retried).toEqual([first!.event_id]);
  await expect.poll(() => rows(gallery.database, "SELECT event_id, listing_id FROM implicit_signals WHERE kind = 'impression'")
    .filter((row: { listing_id: string }) => row.listing_id === first!.listing_id)).toEqual([first]);
});

test('a visible card records an impression after one continuous second', async ({ page, gallery }) => {
  await page.clock.install();
  await page.goto(gallery.url);
  await expect(page.getByRole('article').first()).toBeVisible();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.getByRole('article').first().scrollIntoViewIfNeeded();
  await page.getByRole('article').first().evaluate(element => new Promise<void>(resolve => {
    const observer = new IntersectionObserver(entries => {
      if (entries[0].intersectionRatio >= 0.5) { observer.disconnect(); resolve(); }
    }, { threshold: 0.5 });
    observer.observe(element);
  }));
  await page.clock.runFor(900);
  expect(rows(gallery.database, "SELECT * FROM implicit_signals WHERE kind = 'impression'")).toHaveLength(0);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(2000);
  expect(rows(gallery.database, "SELECT * FROM implicit_signals WHERE kind = 'impression'")).toHaveLength(0);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(900);
  expect(rows(gallery.database, "SELECT * FROM implicit_signals WHERE kind = 'impression'")).toHaveLength(0);
  await page.clock.runFor(200);
  await expect.poll(() => rows(gallery.database, "SELECT * FROM implicit_signals WHERE kind = 'impression'").length).toBeGreaterThan(0);
  // Every card that is half visible posts its own impression on its own timer, so the requests of the other cards
  // may still be in flight when the first row lands: the count is taken once the network is quiet.
  await page.waitForLoadState('networkidle');
  const recorded = rows(gallery.database, "SELECT listing_id FROM implicit_signals WHERE kind = 'impression'");
  expect(new Set(recorded.map((row: { listing_id: string }) => row.listing_id)).size).toBe(recorded.length);
  await page.clock.runFor(5000);
  await page.waitForLoadState('networkidle');
  expect(rows(gallery.database, "SELECT * FROM implicit_signals WHERE kind = 'impression'")).toHaveLength(recorded.length);
});

test('favorite and dismiss offer an optional reason that becomes a normal user note', async ({ page, gallery }, testInfo) => {
  await page.goto(gallery.url);
  const card = page.getByRole('article').filter({ hasText: 'Sunlit home in Lincoln Park' });
  await card.getByRole('button', { name: 'Favorite Sunlit home in Lincoln Park', exact: true }).click();
  await card.getByLabel('Why did you favorite this place? (optional)').fill('I love the big windows.');
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('reason-desktop.png'), fullPage: true });
  await card.getByRole('button', { name: 'Save reason', exact: true }).click();
  await expect.poll(() => rows(gallery.database, "SELECT text FROM notes WHERE role = 'user'").map((row: { text: string }) => row.text)).toContain('I love the big windows.');
  await card.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  const details = page.getByRole('dialog');
  await details.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await details.getByLabel('What ruled this place out? (optional)').fill('The bedrooms are too small.');
  await page.setViewportSize({ width: 390, height: 844 });
  await details.getByLabel('What ruled this place out? (optional)').scrollIntoViewIfNeeded();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('reason-mobile.png'), fullPage: true });
  await details.getByRole('button', { name: 'Save reason', exact: true }).click();
  await expect(details.getByText('The bedrooms are too small.', { exact: true })).toBeVisible();
});

test('a reason can be skipped or retried after a save failure', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  const card = page.getByRole('article').filter({ hasText: 'Sunlit home in Lincoln Park' });
  await card.getByRole('button', { name: 'Favorite Sunlit home in Lincoln Park', exact: true }).click();
  await card.getByRole('button', { name: 'Skip', exact: true }).click();
  expect(rows(gallery.database, "SELECT * FROM notes WHERE role = 'user'")).toHaveLength(0);
  await card.getByRole('button', { name: 'Unfavorite Sunlit home in Lincoln Park', exact: true }).click();
  await card.getByRole('button', { name: 'Favorite Sunlit home in Lincoln Park', exact: true }).click();
  await card.getByLabel('Why did you favorite this place? (optional)').fill('Good layout.');
  await page.route('**/api/listings/*/notes', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Try again' }) }), { times: 1 });
  await card.getByRole('button', { name: 'Save reason', exact: true }).click();
  await expect(card.getByRole('alert')).toContainText('not saved');
  await expect(card.getByLabel('Why did you favorite this place? (optional)')).toHaveValue('Good layout.');
  await card.getByRole('button', { name: 'Save reason', exact: true }).click();
  await expect.poll(() => rows(gallery.database, "SELECT * FROM notes WHERE role = 'user'").length).toBe(1);
});
