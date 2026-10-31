import { expect, type Locator, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test } from './fixtures';

function stored(database: string, code: string) {
  return JSON.parse(execFileSync('python', ['-c', `
import sys, json
sys.path.insert(0, sys.argv[1])
from test_rerank import ListingStore, SETTINGS, fill, listing, extraction
from rerank import rerank, save_review
store = ListingStore(sys.argv[2])
${code}
`, resolve('../tests'), database], { encoding: 'utf8' }));
}

// The spec's two seconds are judged on the owner's PC; a slower machine, such as a shared CI runner, gets the budget
// scaled by tests/test_rerank.py's speed_factor, the same calibration the Python suite's timing tests use.
function speedFactor(): number {
  return Number(execFileSync('python', ['-c', `
import sys
sys.path.insert(0, sys.argv[1])
from test_rerank import speed_factor
print(speed_factor())
`, resolve('../tests')], { encoding: 'utf8' }));
}

function seed(database: string, count = 30, favorites = 5) {
  stored(database, `
store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
for item in store.state()['listings']:
    store.set_matches_criteria(item['id'], False)
fill(store, ${count}, ${favorites})
store.save_geocodes({'Work address': {'latitude': 41.88, 'longitude': -87.63, 'found': 'Work'}})
store.update_settings(places=[{'name': 'Work', 'address': 'Work address'}], location_priorities={**SETTINGS['location_priorities'], 'places': 'high'})
print('null')
`);
}

async function scrolledDown(region: Locator) {
  // A keyboard scroll animates, and Chromium drops a key that lands on the animation's last frame, so the next key
  // waits until the position has stopped moving.
  let previous = -1;
  await expect.poll(async () => {
    const top = await region.evaluate(element => element.scrollTop);
    const settled = top > 0 && top === previous;
    previous = top;
    return settled;
  }, { intervals: [200] }).toBe(true);
}

async function open(page: Page, url: string) {
  await page.route('https://photos.example.com/**', route => route.fulfill({ path: resolve('public/media/living.jpg'), contentType: 'image/jpeg' }));
  await page.goto(url);
}

test('Score and rerank applies real ranks and every favorite check within two seconds', async ({ page, gallery }) => {
  test.setTimeout(60000);
  seed(gallery.database, 300, 50);
  await open(page, gallery.url);
  await expect(page.getByText('No rerank yet', { exact: true })).toBeVisible();
  await expect(page.getByRole('article')).toHaveCount(24);
  const unexpected: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/chat')) unexpected.push(request.url()); });
  await page.getByRole('button', { name: 'Score and rerank', exact: true }).evaluate(button => {
    button.addEventListener('click', () => performance.mark('rerank-start'), { once: true });
    const observer = new MutationObserver(() => {
      if (document.querySelectorAll('[data-testid="held-out-rank"]').length === 50 && document.querySelector('article .rank-badge')?.textContent === '#1 match') {
        observer.disconnect();
        requestAnimationFrame(() => performance.measure('rerank-visible', 'rerank-start'));
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
  const response = page.waitForResponse(response => response.url().endsWith('/api/rerank') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Score and rerank', exact: true }).click();
  const completed = await (await response).json();
  await page.waitForFunction(() => performance.getEntriesByName('rerank-visible').length > 0);
  const elapsed = await page.evaluate(() => performance.getEntriesByName('rerank-visible')[0].duration);
  const factor = speedFactor();
  console.info(`Rerank click to visible order and all 50 held-out ranks: ${elapsed.toFixed(0)}ms; server: ${completed.seconds}s; budget ${(2000 * factor).toFixed(0)}ms`);
  expect(elapsed).toBeLessThan(2000 * factor);
  await expect(page.getByText('300 ranked', { exact: true })).toBeVisible();
  await expect(page.getByRole('article').first().getByText('#1 match', { exact: true })).toBeVisible();
  await expect(page.getByTestId('held-out-rank')).toHaveCount(50);
  const { summary, order } = stored(gallery.database, "print(json.dumps({'summary': store.last_rerank(), 'order': [item['title'] for item in store.browse('Rankings')['items']]}))");
  expect(await page.getByTestId('held-out-rank').allTextContents()).toEqual(summary.held_out.map((item: { rank: number }) => `#${item.rank}`));
  expect(await page.getByRole('article').getByRole('heading').allTextContents()).toEqual(order);
  await expect(page.getByText('Waiting for review', { exact: true })).toBeVisible();
  await expect(page.getByText('Exploration · Shown to learn your taste', { exact: true })).toHaveCount(2);
  expect(unexpected).toEqual([]);
});

test('rerank failures can be retried and pending requests cannot be submitted twice', async ({ page, gallery }) => {
  seed(gallery.database, 6, 2);
  await open(page, gallery.url);
  await page.route('**/api/rerank', route => route.request().method() === 'POST' ?
    route.fulfill({ status: 503, json: { error: 'Scoring is temporarily unavailable.' } }) : route.continue());
  await page.getByRole('button', { name: 'Score and rerank', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Scoring is temporarily unavailable.');
  await page.unroute('**/api/rerank');
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let writes = 0;
  await page.route('**/api/rerank', async route => {
    if (route.request().method() === 'POST') { writes++; await held; }
    await route.continue();
  });
  await page.getByRole('button', { name: 'Retry rerank', exact: true }).click();
  const button = page.getByRole('button', { name: 'Scoring and reranking', exact: true });
  await expect(button).toBeDisabled();
  await button.evaluate(element => { (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click(); });
  release();
  await expect(page.getByText('6 ranked', { exact: true })).toBeVisible();
  expect(writes).toBe(1);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('loading, read errors and a rerank in another tab have distinct recoverable states', async ({ page, gallery }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/rerank', async route => { await held; await route.abort(); });
  await open(page, gallery.url);
  await expect(page.getByLabel('Loading ranking status')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Score and rerank', exact: true })).toBeDisabled();
  release();
  await expect(page.getByRole('alert')).toContainText('Could not load ranking status');
  await expect(page.getByText('No rerank yet', { exact: true })).toHaveCount(0);
  await page.unroute('**/api/rerank');
  await page.route('**/api/rerank', route => route.fulfill({ json: { running: true, last_rerank: null } }));
  await page.getByRole('button', { name: 'Retry ranking status', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Scoring and reranking', exact: true })).toBeDisabled();
  await page.unroute('**/api/rerank');
  await expect(page.getByRole('button', { name: 'Score and rerank', exact: true })).toBeEnabled();
});

test('a reason lists each value it names as one item, the station distance with its station', async ({ page, gallery }, testInfo) => {
  seed(gallery.database);
  const listing = stored(gallery.database, `
store.save_stations('https://www.transitchicago.com/downloads/sch_data/google_transit.zip', [{'id': '41020', 'name': 'Logan Square', 'latitude': 41.929728, 'longitude': -87.708541}])
store.update_settings(location_priorities={**SETTINGS['location_priorities'], 'rapid_transit': 'high', 'places': 'high'})
rerank(store, seed=1)
shown = [store.listing(item['id']) for item in store.browse('Rankings')['items']]
print(json.dumps(next(item for item in shown if any(value.endswith(' km to Logan Square station') for value in item['reason']['features']['for'] + item['reason']['features']['against']))))
`);
  const { for: up, against: down } = listing.reason.features;
  await open(page, gallery.url);
  const card = page.getByRole('article').filter({ has: page.getByRole('heading', { name: listing.title, exact: true }) });
  const details = page.getByRole('dialog');
  for (const scope of [card, details]) {
    if (scope === details) await card.getByRole('button', { name: `View ${listing.title}`, exact: true }).click();
    for (const [name, values] of [['For it', up], ['Against it', down]] as const) {
      if (values.length) await expect(scope.getByRole('list', { name, exact: true }).getByRole('listitem')).toHaveText(values);
    }
  }
  await expect(details.getByRole('listitem').filter({ hasText: /^\d+\.\d km to Logan Square station$/ })).toHaveCount(1);
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await expect(details).toBeHidden();
  await card.screenshot({ path: testInfo.outputPath('reason-lists.png') });
});

test('stored reviews refresh order, reasons and open details without losing notes or history', async ({ page, gallery }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  seed(gallery.database);
  const chosen = stored(gallery.database, `
summary = rerank(store, seed=1)
ranked = store.ranked_listings()
identifier = next(item for item in sorted(ranked, key=lambda item: ranked[item]['rank']) if item not in [f'zillow:{n:04}' for n in range(5)] and not ranked[item]['exploration'])
store.add_note('Keep this note through the review.', listing_id=identifier)
store.request_contact(identifier)
store.correspond(identifier, 'incoming', 'email', 'The courtyard is available for a viewing.', external_message_id='rerank-message')
print(json.dumps(store.listing(identifier)))
`);
  await open(page, gallery.url);
  await page.getByRole('button', { name: `View ${chosen.title}`, exact: true }).click();
  const details = page.getByRole('dialog');
  await expect(details.getByText('Keep this note through the review.', { exact: true })).toBeVisible();
  await details.getByRole('textbox', { name: 'Your note', exact: true }).fill('An unsaved thought stays here.');
  stored(gallery.database, "store.hand_review(store.last_rerank()['id']); print('null')");
  await expect(page.getByText('Review in progress', { exact: true })).toBeAttached();
  const result = stored(gallery.database, `
identifier = ${JSON.stringify(chosen.id)}
value = next(part['value'] for part in store.listing(identifier)['explanation']['contributions'] if part['feature'] == 'place:Work')
result = save_review(store, {'rerank': store.last_rerank()['id'], 'moves': [{'id': identifier, 'to_rank': 30, 'reason': f'The model weighs [{value}] too much.', 'confidence': 0.8}], 'reasons': [{'id': identifier, 'reason': f'Strong on [{value}].'}], 'flags': [{'id': identifier, 'kind': 'dislike', 'preference': 'Carpet'}], 'findings': ['[vaulted ceiling] and [sloped ceiling] mean the same thing.']})
print(json.dumps({'review': result, 'listing': store.listing(identifier), 'order': [item['title'] for item in store.browse('Rankings')['items']]}))
`);
  expect(result.review.outcome).toBe('kept');
  await expect(details.getByRole('heading', { name: 'Why it ranks #30', exact: true })).toBeVisible();
  await expect(details.getByText(result.listing.reason.features.text, { exact: true })).toBeVisible();
  await expect(details.getByText('Keep this note through the review.', { exact: true })).toBeVisible();
  await expect(details.getByText('The courtyard is available for a viewing.', { exact: true })).toBeVisible();
  await expect(details.getByRole('textbox', { name: 'Your note', exact: true })).toHaveValue('An unsaved thought stays here.');
  await expect(details.getByRole('alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Close listing details' }).click();
  expect(await page.getByRole('article').getByRole('heading').allTextContents()).toEqual(result.order);
  await page.getByRole('button', { name: 'Review complete', exact: true }).click();
  await expect(page.getByText('Review moves kept', { exact: true })).toBeVisible();
  await expect(page.getByText(result.review.moves[0].reason, { exact: true })).toBeVisible();
  await expect(page.getByText('Ranks against what you dislike: Carpet', { exact: true })).toBeVisible();
  await expect(page.getByText('vaulted ceiling and sloped ceiling mean the same thing.', { exact: true })).toBeVisible();
  await page.route('**/api/rerank', route => route.abort());
  await page.getByRole('button', { name: 'Refresh listings', exact: true }).click();
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
  await expect(page.getByRole('alert').filter({ hasText: 'Could not load ranking status' })).toBeVisible();
  await expect(page.getByText('Showing the last loaded result. Review updates may be missing.')).toBeVisible();
  await expect(page.getByText('Review moves kept', { exact: true })).toBeVisible();
  await page.unroute('**/api/rerank');
  await page.getByRole('button', { name: 'Retry ranking status' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Could not load ranking status' })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('a new rerank supersedes a pending review, and failed reviews keep usable scoring results', async ({ page, gallery }) => {
  seed(gallery.database, 6, 0);
  const first = stored(gallery.database, 'print(json.dumps(rerank(store, seed=1)))');
  await open(page, gallery.url);
  await expect(page.getByText('No favorites to check yet.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Score and rerank', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Score and rerank', exact: true })).toBeEnabled();
  const status = stored(gallery.database, `
with store.connect() as connection:
    previous = connection.execute('SELECT state FROM reviews WHERE rerank_id = ?', (${first.id},)).fetchone()[0]
store.fail_review(store.last_rerank()['id'], 'The review was not saved in 3 attempts.')
print(json.dumps({'previous': previous, 'current': store.last_rerank()}))
`);
  expect(status.previous).toBe('superseded');
  await page.getByRole('button', { name: 'Review failed', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('The review was not saved in 3 attempts.');
  await expect(page.getByText('6 ranked', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Score and rerank', exact: true }).click();
  await expect(page.getByText('Waiting for review', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('unranked reasons remain visible without analysis, and Compare has no ranking cues', async ({ page, gallery }) => {
  seed(gallery.database, 5, 1);
  stored(gallery.database, `
from store import extraction_read
rerank(store, seed=1)
saved = store.upsert_listing(listing(id='ready', title='Ready for reranking', photos=[]))
store.save_extraction('ready', saved['content_fingerprint'], extraction(0), photos=[], phrases=[])
store.upsert_listing(listing(id='waiting', title='Waiting for features', photos=[]))
failed = store.upsert_listing(listing(id='failed', title='Extraction failed', photos=[]))
for _ in range(3):
    store.record_read_attempts([extraction_read('failed', failed['content_fingerprint'])], [])
store.upsert_listing(listing(id='zillow:0000', title='Changed apartment', photos=[]))
print('null')
`);
  await open(page, gallery.url);
  for (const [name, reason] of [['Ready for reranking', 'new or changed since the last rerank'], ['Waiting for features', 'waiting for its feature record'], ['Extraction failed', 'extraction failed 3 times'], ['Changed apartment', 'waiting for its feature record']]) {
    await expect(page.getByRole('article').filter({ has: page.getByRole('heading', { name, exact: true }) })).toContainText(reason);
  }
  await page.getByRole('button', { name: 'View Extraction failed', exact: true }).click();
  await expect(page.getByRole('dialog').getByText(/Not ranked yet: extraction failed/)).toBeVisible();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.getByRole('tab', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(2);
  await expect(page.getByRole('region', { name: 'Ranking controls and results' })).toHaveCount(0);
  await expect(page.getByText(/#\d+ match|Shown to learn your taste|Most similar favorite/)).toHaveCount(0);
  await page.getByRole('button', { name: 'View details', exact: true }).first().click();
  await expect(page.getByRole('dialog').getByText(/Why it ranks|feature contributions|Most similar favorite|Shown to learn your taste/)).toHaveCount(0);
});

for (const viewport of [{ name: 'desktop', width: 1440, height: 1100 }, { name: 'mobile', width: 390, height: 844 }]) {
  test(`rerank results and full explanations are accessible on ${viewport.name}`, async ({ page, gallery }, testInfo) => {
    seed(gallery.database);
    const saved = stored(gallery.database, `
summary = rerank(store, seed=1)
ranked = store.ranked_listings()
favorite = min([f'zillow:{n:04}' for n in range(5)], key=lambda identifier: ranked[identifier]['rank'])
first = min(ranked, key=lambda identifier: ranked[identifier]['rank'])
value = next(part['value'] for part in ranked[first]['explanation']['contributions'] if part['feature'] == 'place:Work')
result = save_review(store, {'rerank': summary['id'], 'moves': [{'id': favorite, 'to_rank': 30, 'reason': 'The model weighs [price] too little.', 'confidence': 0.7}], 'reasons': [{'id': first, 'reason': f'Strong on [{value}].'}], 'flags': [], 'findings': ['Favorites rank poorly when held out.']})
print(json.dumps({'review': result, 'listing': store.listing(first)}))
`);
    expect(saved.review.outcome).toBe('reverted');
    await page.setViewportSize(viewport);
    await open(page, gallery.url);
    await expect(page.getByText('30 ranked', { exact: true })).toBeVisible();
    await expect(page.getByRole('article').first().getByRole('link', { name: 'OpenStreetMap contributors', exact: true })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    if (viewport.name === 'mobile') await page.getByRole('region', { name: 'Ranking controls and results' }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`rerank-${viewport.name}.png`) });
    const favorites = page.getByRole('region', { name: 'All favorites and held-out ranks', exact: true });
    await favorites.press('End');
    await scrolledDown(favorites);
    const viewportBounds = await favorites.boundingBox();
    const headerBounds = await favorites.getByRole('row').first().boundingBox();
    expect(viewportBounds!.height).toBeLessThanOrEqual(190);
    expect(Math.abs(headerBounds!.y - viewportBounds!.y)).toBeLessThan(2);
    await favorites.press('Home');
    await expect.poll(() => favorites.evaluate(element => element.scrollTop)).toBe(0);
    await page.getByRole('button', { name: 'Favorite check', exact: false }).click();
    await page.getByRole('button', { name: 'Review complete', exact: true }).click();
    await expect(page.getByText('Review moves reverted', { exact: true })).toBeVisible();
    await expect(page.getByText('The model weighs price too little.', { exact: true })).toBeVisible();
    for (const metric of ['mean', 'median'] as const) {
      const row = page.getByRole('row').filter({ has: page.getByRole('rowheader', { name: metric === 'mean' ? 'Mean' : 'Median', exact: true }) });
      await expect(row.getByRole('cell')).toHaveText([saved.review.check.before[metric], saved.review.check.after[metric]].map(value => new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)));
    }
    await page.getByRole('region', { name: 'Ranking controls and results' }).scrollIntoViewIfNeeded();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`rerank-review-${viewport.name}.png`) });
    await page.getByRole('button', { name: `View ${saved.listing.title}`, exact: true }).click();
    const details = page.getByRole('dialog');
    await expect(details.getByText(saved.listing.explanation.similar_favorite.title, { exact: true })).toBeVisible();
    await details.getByRole('button', { name: /All \d+ feature contributions/ }).click();
    const rows = details.getByRole('row');
    await expect(rows).toHaveCount(saved.listing.explanation.contributions.length + 1);
    for (const part of saved.listing.explanation.contributions) {
      const row = rows.filter({ has: page.getByText(part.feature.replaceAll('_', ' ').replaceAll(':', ': '), { exact: true }) });
      await expect(row.getByRole('cell').last()).toHaveText(new Intl.NumberFormat(undefined, { maximumFractionDigits: 4, signDisplay: 'exceptZero' }).format(part.contribution));
      if (part.feature === 'place:Work') await expect(row.getByRole('link', { name: 'OpenStreetMap contributors', exact: true })).toBeVisible();
    }
    await details.getByRole('heading', { name: /Why it ranks/ }).scrollIntoViewIfNeeded();
    await details.evaluate(element => { element.scrollTop -= 100; });
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`explanation-${viewport.name}.png`) });
  });
}
