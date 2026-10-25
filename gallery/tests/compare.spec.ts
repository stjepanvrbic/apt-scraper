import { expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test } from './fixtures';

function seedCompare(database: string, count = 3) {
  execFileSync('python', ['-c', `
import sys
sys.path.insert(0, sys.argv[1])
from test_compare import add_choices, ListingStore, listing, extraction
store = ListingStore(sys.argv[2])
count = int(sys.argv[3])
add_choices(store, count)
for number in range(count):
    identifier = f'choice:{number:02}'
    photos = ['/media/living.jpg' if number % 2 == 0 else '/media/kitchen.jpg']
    saved = store.upsert_listing(listing(id=identifier, title=f'Courtyard apartment {number + 1}', address=f'{100 + number} Example Street, Chicago', price=1800 + number * 100, photos=photos))
    store.save_extraction(identifier, saved['content_fingerprint'], extraction(1), photos=photos, phrases=[])
    store.rank(identifier, saved['content_fingerprint'], number + 1, 'A ranking reason that must not bias a comparison.')
`, resolve('../tests'), database, String(count)]);
}

test('Compare saves either choice, skips, and persists progress across restarts without reranking', async ({ page, gallery }) => {
  seedCompare(gallery.database);
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Which of these two?' })).toBeVisible();
  await expect(page.getByRole('article')).toHaveCount(2);
  await expect(page.getByText('Your choices apply at the next rerank.')).toBeVisible();
  await expect(page.getByRole('article').getByText(/match|Awaiting ranking|must not bias/)).toHaveCount(0);
  const firstPair = await page.getByRole('article').getByRole('heading').allTextContents();
  await page.getByRole('button', { name: 'Choose this apartment' }).first().focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText('Choice saved');
  await expect(page.getByText('1 choice saved', { exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole('article').getByRole('heading').allTextContents()).not.toEqual(firstPair);
  await page.getByRole('button', { name: 'Choose this apartment' }).last().click();
  await expect(page.getByText('2 choices saved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Skip this pair', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Pair skipped');
  await expect(page.getByRole('heading', { name: 'No comparisons ready' })).toBeVisible();
  await page.goto(await gallery.restart());
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByText('2 choices saved', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No comparisons ready' })).toBeVisible();
  const saved = JSON.parse(execFileSync('python', ['-c', `import sys,json; sys.path.insert(0,sys.argv[1]); from test_compare import ListingStore; store=ListingStore(sys.argv[2]); print(json.dumps({'answers':store.pairwise_answers(),'rank':store.listing('choice:00')['rank'],'rerank':store.last_rerank()}))`, resolve('../tests'), gallery.database], { encoding: 'utf8' }));
  expect(saved.answers).toHaveLength(2);
  expect(saved.rank).toBe(1);
  expect(saved.rerank).toBeNull();
});

for (const action of ['answer', 'skip']) {
  test(`Compare retains the pair after a failed ${action}, retries, and prevents duplicate saves`, async ({ page, gallery }) => {
    seedCompare(gallery.database);
    await page.goto(gallery.url);
    await page.getByRole('tab', { name: 'Compare', exact: true }).click();
    await expect(page.getByRole('article')).toHaveCount(2);
    const pair = await page.getByRole('article').getByRole('heading').allTextContents();
    const actionButton = action === 'answer' ? page.getByRole('button', { name: 'Choose this apartment' }).first() : page.getByRole('button', { name: 'Skip this pair' });
    await page.route(`**/api/compare/${action}`, route => route.fulfill({ status: 503, json: { error: 'Save is temporarily unavailable.' } }));
    await actionButton.click();
    await expect(page.getByRole('alert')).toContainText('Could not save');
    expect(await page.getByRole('article').getByRole('heading').allTextContents()).toEqual(pair);
    await expect(page.getByText('0 choices saved', { exact: true })).toBeVisible();
    await page.unroute(`**/api/compare/${action}`);
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    let writes = 0;
    await page.route(`**/api/compare/${action}`, async route => { writes++; await held; await route.continue(); });
    await page.getByRole('button', { name: 'Retry save', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Skip this pair' })).toBeDisabled();
    for (const button of await page.getByRole('button', { name: 'Choose this apartment' }).all()) await expect(button).toBeDisabled();
    await actionButton.evaluate(element => { (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click(); });
    release();
    await expect(page.getByRole('status')).toContainText(action === 'answer' ? 'Choice saved' : 'Pair skipped');
    await expect.poll(() => page.getByRole('article').getByRole('heading').allTextContents()).not.toEqual(pair);
    expect(writes).toBe(1);
    await expect(page.getByRole('alert')).toHaveCount(0);
  });
}

test('Compare distinguishes loading, read errors, retries and an empty sample store', async ({ page, gallery }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/compare', async route => { await held; await route.abort(); });
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByLabel('Loading comparison')).toBeVisible();
  release();
  await expect(page.getByRole('alert')).toContainText('Could not load comparison');
  await expect(page.getByRole('heading', { name: 'No comparisons ready' })).toHaveCount(0);
  await page.unroute('**/api/compare');
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'No comparisons ready' })).toBeVisible();
  await expect(page.getByText('0 choices saved', { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test('Compare never offers the saved pair again when loading the next question fails', async ({ page, gallery }) => {
  seedCompare(gallery.database);
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  await page.route('**/api/compare', route => route.abort());
  await page.getByRole('button', { name: 'Choose this apartment' }).first().click();
  await expect(page.getByRole('status')).toContainText('Choice saved');
  await expect(page.getByRole('alert')).toContainText('Could not load comparison');
  await expect(page.getByRole('button', { name: 'Choose this apartment' })).toHaveCount(0);
  await page.unroute('**/api/compare');
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  await expect(page.getByText('1 choice saved', { exact: true })).toBeVisible();
});

test('Compare refreshes eligibility after dismissing a listing in its drawer', async ({ page, gallery }) => {
  seedCompare(gallery.database);
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  const dismissed = await page.getByRole('article').first().getByRole('heading').innerText();
  await page.getByRole('button', { name: `View ${dismissed}`, exact: true }).click();
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/listings/*/feedback', async route => { await held; await route.continue(); });
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect(page.getByRole('button', { name: 'Skip this pair' })).toBeDisabled();
  for (const button of await page.getByRole('button', { name: 'Choose this apartment' }).all()) await expect(button).toBeDisabled();
  release();
  await expect(page.getByRole('article').getByRole('heading', { name: dismissed, exact: true })).toHaveCount(0);
  await expect(page.getByRole('article')).toHaveCount(2);
  await page.getByRole('button', { name: 'Choose this apartment' }).first().click();
  await expect(page.getByRole('status')).toContainText('Choice saved');
  await expect(page.getByRole('heading', { name: 'No comparisons ready' })).toBeVisible();
});

test('Compare drops a failed save retry when drawer feedback replaces the pair', async ({ page, gallery }) => {
  seedCompare(gallery.database);
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await page.route('**/api/compare/answer', route => route.abort());
  await page.getByRole('button', { name: 'Choose this apartment' }).first().click();
  await expect(page.getByRole('button', { name: 'Retry save' })).toBeVisible();
  await page.getByRole('button', { name: /^View Courtyard apartment/ }).first().click();
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('Dismissed', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  await expect(page.getByRole('button', { name: 'Retry save' })).toHaveCount(0);
  await page.unroute('**/api/compare/answer');
  await page.getByRole('button', { name: 'Choose this apartment' }).first().click();
  await expect(page.getByRole('status')).toContainText('Choice saved');
});

test('Compare focuses the next question once and preserves focus during later refreshes', async ({ page, gallery }) => {
  seedCompare(gallery.database);
  await page.clock.install();
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await page.getByRole('button', { name: 'Choose this apartment' }).first().click();
  await expect(page.getByRole('heading', { name: 'Which of these two?' })).toBeFocused();
  const tab = page.getByRole('tab', { name: 'Compare', exact: true });
  await tab.focus();
  execFileSync('python', ['-c', `import sys; sys.path.insert(0,sys.argv[1]); from test_compare import ListingStore; ListingStore(sys.argv[2]).add_note('A new note.',listing_id='choice:00')`, resolve('../tests'), gallery.database]);
  const refreshed = page.waitForResponse(response => response.url().endsWith('/api/compare'));
  await page.clock.fastForward(16000);
  await refreshed;
  await expect(page.getByRole('button', { name: 'Skip this pair' })).toBeEnabled();
  await expect(tab).toBeFocused();
});

test('Compare observes new feature records and eligibility changes through revision polling', async ({ page, gallery }) => {
  await page.clock.install();
  let reads = 0;
  page.on('request', request => { if (request.url().endsWith('/api/compare')) reads++; });
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'No comparisons ready' })).toBeVisible();
  const before = reads;
  await page.clock.fastForward(16000);
  expect(reads).toBe(before);
  seedCompare(gallery.database);
  await page.clock.fastForward(16000);
  await expect(page.getByRole('article')).toHaveCount(2);
  execFileSync('python', ['-c', `import sys; sys.path.insert(0,sys.argv[1]); from test_compare import ListingStore; ListingStore(sys.argv[2]).set_availability('choice:00','unavailable')`, resolve('../tests'), gallery.database]);
  await page.clock.fastForward(16000);
  await expect(page.getByRole('article').getByRole('heading', { name: 'Courtyard apartment 1', exact: true })).toHaveCount(0);
  await expect(page.getByRole('article')).toHaveCount(2);
});

test('Compare cards and details are accessible on desktop and mobile', async ({ page, gallery }, testInfo) => {
  seedCompare(gallery.database);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  await expect.poll(() => page.locator('article img').evaluateAll(images => images.length === 2 && images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
  const cards = page.getByRole('article');
  for (const card of await cards.all()) {
    await expect(card.getByText('850 sq ft', { exact: false })).toBeVisible();
    await expect(card.getByText('2 bedrooms, 1 bathroom', { exact: false })).toBeVisible();
    await expect(card.getByText(/Example Street, Chicago/)).toBeVisible();
    await expect(card.getByText(/\$1,[89]00/)).toBeVisible();
  }
  const left = await cards.first().boundingBox();
  const right = await cards.last().boundingBox();
  expect(left!.y).toBe(right!.y);
  expect(left!.width).toBe(right!.width);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('compare-desktop.png'), fullPage: true });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    for (const tab of await page.getByRole('tab').all()) await expect(tab).toBeInViewport();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`compare-mobile-${width}.png`), fullPage: true });
  }
  for (const button of await page.getByRole('button', { name: /^View Courtyard apartment/ }).all()) {
    await button.click();
    const details = page.getByRole('dialog');
    await expect(details.getByRole('heading', { name: /Courtyard apartment/ })).toBeVisible();
    await expect(details.getByText('Bright rooms facing a shared courtyard.')).toBeVisible();
    await expect(details.getByText(/Why it ranks|must not bias/)).toHaveCount(0);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath('compare-details-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: 'Close listing details' }).click();
    await expect(button).toBeFocused();
  }
});
