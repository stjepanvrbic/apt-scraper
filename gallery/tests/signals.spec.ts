import { expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { test } from './fixtures';

type Signal = { kind: string; visit_id: string; duration_ms: number | null; photo_url: string | null };

function signals(database: string): Signal[] {
  return JSON.parse(execFileSync('python', ['-c', `import sys,sqlite3,json
connection=sqlite3.connect(sys.argv[1])
connection.row_factory=sqlite3.Row
print(json.dumps([dict(row) for row in connection.execute('SELECT * FROM implicit_signals ORDER BY rowid')]))`, database], { encoding: 'utf8' }));
}

async function visibility(page: Page, state: 'hidden' | 'visible') {
  await page.evaluate(value => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value });
    document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
  }, state);
}

test('details batch opens, visible dwell and actual photo views without changing the gallery', async ({ page, gallery }, testInfo) => {
  await page.clock.install();
  const batches: unknown[][] = [];
  page.on('request', request => {
    if (request.url().endsWith('/api/signals')) {
      expect(new URL(request.url()).origin).toBe(new URL(gallery.url).origin);
      expect(request.headers().authorization).toMatch(/^Bearer .+/);
      batches.push(request.postDataJSON().events);
    }
  });
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('$2,450')).toBeVisible();
  await page.getByRole('button', { name: 'View all 3 photos' }).click();
  await expect(page.getByText('1 / 3', { exact: true })).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByText('2 / 3', { exact: true })).toBeVisible();
  await page.clock.runFor(2000);
  expect(batches).toHaveLength(0);
  await visibility(page, 'hidden');
  await expect.poll(() => signals(gallery.database).filter(row => row.kind === 'photo').length).toBe(2);
  const first = signals(gallery.database);
  expect(first.filter(row => row.kind === 'open')).toHaveLength(1);
  expect(first.filter(row => row.kind === 'photo').map(row => row.photo_url)).toEqual(['/media/living.jpg', '/media/kitchen.jpg']);
  const dwell = () => signals(gallery.database).reduce((sum, row) => sum + (row.duration_ms ?? 0), 0);
  const before = dwell();
  expect(before).toBeGreaterThanOrEqual(2000);
  await page.clock.runFor(60000);
  expect(dwell()).toBe(before);
  await visibility(page, 'visible');
  await page.clock.runFor(3000);
  await page.keyboard.press('Escape');
  await page.screenshot({ path: testInfo.outputPath('instrumented-details.png') });
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect.poll(() => signals(gallery.database).filter(row => row.kind === 'close').length).toBe(1);
  expect(dwell() - before).toBeGreaterThanOrEqual(3000);
  expect(dwell() - before).toBeLessThan(6000);
  expect(new Set(signals(gallery.database).map(row => row.visit_id)).size).toBe(1);
  expect(batches.every(batch => batch.length <= 20)).toBe(true);
  expect(batches.length).toBeLessThan(signals(gallery.database).length);
});

test('lost batch replies retry without duplicates or a visible save error', async ({ page, gallery }) => {
  await page.clock.install();
  await page.goto(gallery.url);
  let attempts = 0;
  const failed = page.waitForEvent('console', message => message.text().includes('the batch will be retried'));
  await page.route('**/api/signals', async route => {
    attempts++;
    if (attempts === 1) { await route.fetch(); await route.abort(); }
    else await route.continue();
  });
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('$2,450')).toBeVisible();
  await page.clock.runFor(1200);
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect.poll(() => attempts).toBe(1);
  await failed;
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.clock.runFor(11000);
  await expect.poll(() => attempts).toBe(2);
  const saved = signals(gallery.database);
  expect(saved.filter(row => row.kind === 'open')).toHaveLength(1);
  expect(saved.filter(row => row.kind === 'close')).toHaveLength(1);
});

test('pagehide flushes the open visit through authenticated keepalive', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('$2,450')).toBeVisible();
  await page.goto('about:blank');
  await expect.poll(() => signals(gallery.database).filter(row => row.kind === 'open').length).toBe(1);
  expect(signals(gallery.database).some(row => row.kind === 'dwell')).toBe(true);
});

test('refreshing details does not open another visit, while reopening does', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  const open = page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true });
  await open.click();
  await expect(page.getByRole('dialog').getByText('$2,450')).toBeVisible();
  await page.getByRole('button', { name: 'Favorite', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unfavorite', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect.poll(() => signals(gallery.database).filter(row => row.kind === 'close').length).toBe(1);
  expect(signals(gallery.database).filter(row => row.kind === 'open')).toHaveLength(1);
  await open.click();
  await expect(page.getByRole('dialog').getByText('$2,450')).toBeVisible();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect.poll(() => signals(gallery.database).filter(row => row.kind === 'close').length).toBe(2);
  expect(new Set(signals(gallery.database).map(row => row.visit_id)).size).toBe(2);
});
