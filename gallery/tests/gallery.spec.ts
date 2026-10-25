import { test as base, expect } from '@playwright/test';
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import AxeBuilder from '@axe-core/playwright';

const script = resolve('../profile/skills/gallery/scripts/gallery.py');
const test = base.extend<{ gallery: { url: string; restart: () => Promise<string>; database: string } }>({
  gallery: async ({ browserName }, use) => {
    const directory = await mkdtemp(join(tmpdir(), `apt-gallery-${browserName}-`));
    const database = join(directory, 'sample.sqlite3');
    let process: ChildProcess;
    let address: string;
    const start = async () => {
      process = spawn('python', [script, '--database', database, '--sample', '--port', '0', 'serve']);
      let errors = '';
      process.stderr!.on('data', chunk => { errors += chunk; });
      return new Promise<string>((resolve, reject) => {
        const lines = createInterface({ input: process.stdout! });
        lines.once('line', line => { lines.close(); address = JSON.parse(line).url; resolve(address); });
        process.once('exit', code => reject(new Error(`Gallery exited ${code}: ${errors}`)));
        process.once('error', reject);
      });
    };
    const stop = async () => {
      if (process.exitCode !== null) return;
      const exited = new Promise<void>(resolve => { process.once('exit', () => resolve()); });
      const url = new URL(address);
      await fetch(url.origin + '/api/stop', { method: 'POST', headers: {
        Authorization: `Bearer ${new URLSearchParams(url.hash.slice(1)).get('token')}`, 'Content-Type': 'application/json',
      }, body: '{}' });
      await exited;
    };
    try {
      const url = await start();
      await use({ url, database, restart: async () => { await stop(); return start(); } });
    } finally {
      await stop();
      await rm(directory, { recursive: true, force: true });
    }
  },
});

test('browse ordered tabs, complete listing details, photos and original source', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await expect(page.getByRole('tab')).toHaveText(['Rankings', 'Favorites', 'Contacted', 'Viewings', 'Notes']);
  await expect(page.getByRole('article')).toHaveCount(4);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  const details = page.getByRole('dialog');
  await expect(details.getByText('$2,450')).toBeVisible();
  await expect(details.getByText('920 sq ft')).toBeVisible();
  await expect(details.getByText('2 bedrooms, 1 bathroom')).toBeVisible();
  await expect(details.getByText('1800 Sample Avenue, Lincoln Park, Chicago')).toBeVisible();
  await expect(details.getByText(/South-facing windows/)).toBeVisible();
  await expect(details.getByText('In-unit laundry')).toBeVisible();
  await expect(details.getByText('Available', { exact: true })).toBeVisible();
  await expect(details.getByText(/First seen/)).toBeVisible();
  await expect(details.getByRole('link', { name: 'Sample source' })).toHaveAttribute('href', 'https://example.com/listings/lincoln');
  await expect(details.getByRole('link', { name: 'Sample source' })).toHaveAttribute('target', '_blank');
  await details.getByRole('button', { name: 'View all 3 photos' }).click();
  await expect(page.getByRole('button', { name: 'Next' })).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('2 / 3', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(details.getByRole('heading', { name: 'Sunlit home in Lincoln Park' })).toBeVisible();
});

test('favorites, unfavorites, dismissals and listing notes persist across server restarts', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'Favorite Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('tab', { name: 'Favorites', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByText('1 place', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('textbox', { name: 'Your note', exact: true }).fill('Love the light. Check traffic noise at the viewing.');
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(page.getByText('Love the light. Check traffic noise at the viewing.', { exact: true })).toBeVisible();
  await page.goto(await gallery.restart());
  await page.getByRole('tab', { name: 'Favorites', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(1);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByText('Love the light. Check traffic noise at the viewing.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Unfavorite', exact: true }).click();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.goto(await gallery.restart());
  await page.getByRole('tab', { name: 'Favorites', exact: true }).click();
  await expect(page.getByText('No favorites yet')).toBeVisible();
  await page.getByRole('tab', { name: 'Rankings', exact: true }).click();
  await page.getByRole('button', { name: 'Favorite Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(page.getByText('Dismissed', { exact: true })).toBeVisible();
  await page.goto(await gallery.restart());
  await expect(page.getByRole('article')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Favorites', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(0);
});

test('contact requests, correspondence and viewings share the store', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('button', { name: 'Request contact', exact: true }).click();
  await expect(page.getByText('Request saved. No message has been sent yet.')).toBeVisible();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.getByRole('tab', { name: 'Contacted', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(1);
  execFileSync('python', [script, '--database', gallery.database, 'correspond', 'sample:lincoln', 'incoming', 'email', 'Friday at 3 pm works.', '--message-id', 'reply-1', '--thread-id', 'thread-1']);
  execFileSync('python', ['-c', `import sys; sys.path.insert(0,sys.argv[1]); from store import ListingStore; store=ListingStore(sys.argv[2]); viewing=store.agree_viewing('sample:lincoln','2026-10-30T15:00:00-05:00','Meet in the lobby.'); assert store.record_booking(viewing,'2026-10-30T20:00:00+00:00','Meet in the lobby.','testevent1')`, resolve('../profile/skills/gallery/scripts'), gallery.database]);
  await page.getByRole('button', { name: 'Refresh listings' }).click();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('Friday at 3 pm works.', { exact: true })).toBeVisible();
  await expect(page.getByText('Meet in the lobby.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.getByRole('tab', { name: 'Viewings', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByRole('article').getByText(/Oct 30/)).toBeVisible();
});

test('an agreed time shows in the listing and reaches Viewings once booked', async ({ page, gallery }) => {
  const viewing = execFileSync('python', ['-c', `import sys; sys.path.insert(0,sys.argv[1]); from store import ListingStore; store=ListingStore(sys.argv[2]); store.request_contact('sample:lincoln'); print(store.agree_viewing('sample:lincoln','2026-10-31T11:00:00-05:00','Meet Anna in the lobby.'))`, resolve('../profile/skills/gallery/scripts'), gallery.database], { encoding: 'utf8' }).trim();
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByText('Agreed viewing, not yet in your calendar', { exact: true })).toBeVisible();
  await expect(page.getByText('Meet Anna in the lobby.', { exact: true })).toBeVisible();
  await expect(page.getByText('Sample listings are never contacted.', { exact: true })).toBeVisible();
  await page.getByText('Agreed viewing, not yet in your calendar', { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('agreed-viewing.png') });
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.getByRole('tab', { name: 'Viewings', exact: true }).click();
  await expect(page.getByRole('article')).toHaveCount(0);
  execFileSync('python', ['-c', `import sys; sys.path.insert(0,sys.argv[1]); from store import ListingStore; assert ListingStore(sys.argv[2]).record_booking(sys.argv[3],'2026-10-31T16:00:00+00:00','Meet Anna in the lobby.',sys.argv[3].replace('-',''))`, resolve('../profile/skills/gallery/scripts'), gallery.database, viewing]);
  await page.getByRole('button', { name: 'Refresh listings' }).click();
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByRole('article').getByText(/Oct 31/)).toBeVisible();
  await page.getByRole('article').getByText(/Oct 31/).scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('booked-viewing.png') });
  await page.getByRole('article').getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByText('Booked viewing', { exact: true })).toBeVisible();
});

test('a failed save keeps the draft and reports the failure before retry', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('textbox', { name: 'Your note', exact: true }).fill('Check the windows.');
  await page.route('**/api/listings/*/notes', route => route.abort('failed'));
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Could not reach the gallery');
  await expect(page.getByRole('textbox', { name: 'Your note', exact: true })).toHaveValue('Check the windows.');
  await page.unroute('**/api/listings/*/notes');
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(page.getByText('Check the windows.', { exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Your note', exact: true })).toHaveValue('');
});

test('desktop and mobile are accessible, fit the viewport and load bundled photos', async ({ page, gallery }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(gallery.url);
  await expect(page.getByRole('article')).toHaveCount(4);
  await expect.poll(() => page.locator('article img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.html) }))).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  for (const name of ['Rankings', 'Favorites', 'Contacted', 'Viewings', 'Notes']) {
    await expect(page.getByRole('tab', { name, exact: true })).toBeInViewport();
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true });
  await page.getByRole('tab', { name: 'Rankings' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Favorites' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('No favorites yet')).toBeVisible();
  await page.getByRole('tab', { name: 'Rankings' }).click();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  expect((await new AxeBuilder({ page }).analyze()).violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.html) }))).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('detail-mobile.png'), fullPage: true });
});

test('a broken photo has a readable fallback and source links open a new tab', async ({ page, gallery }) => {
  await page.route('**/media/living.jpg', route => route.abort());
  await page.goto(gallery.url);
  await expect(page.getByText('Photo unavailable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  const popup = page.waitForEvent('popup');
  await page.getByRole('link', { name: 'Sample source', exact: true }).click();
  expect((await popup).url()).toBe('https://example.com/listings/lincoln');
});

test('dismissed listings can be restored after closing their drawer', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.getByRole('button', { name: 'Dismissed listings' }).click();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await page.getByRole('button', { name: 'Restore listing' }).click();
  await page.getByRole('button', { name: 'Close listing details' }).click();
  await page.getByRole('button', { name: 'Back to rankings' }).click();
  await expect(page.getByRole('article')).toHaveCount(4);
});

test('Notes uses a chat composer, streamed reply, tool timeline and choices', async ({ page, gallery }) => {
  const messages: Record<string, unknown>[] = [];
  await page.route('**/api/chat**', async route => {
    const request = route.request();
    if (request.url().includes('/control')) {
      expect(request.postDataJSON().answer).toBe('Oak');
      messages[1] = { ...messages[1], content: 'Oak Street is your preference.', status: 'complete', prompt: null };
    } else if (request.method() === 'POST') {
      expect(request.postDataJSON().text).toBe('I prefer quiet streets.');
      messages.push({ id: 1, role: 'user', content: 'I prefer quiet streets.', status: 'complete', events: [] },
        { id: 2, role: 'agent', content: 'Which street?', status: 'running', events: [{ id: 'tool-1', name: 'read preferences', status: 'complete', output: 'Read stored preferences.' }],
          prompt: { prompt_id: 'question-1', kind: 'clarify', question: 'Choose a street', choices: ['Oak', 'Elm'] } });
    }
    await route.fulfill({ json: { messages, total: messages.length, active: messages.some(item => item.status === 'running') } });
  });
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Notes', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message Hermes' }).fill('I prefer quiet streets.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByText('Which street?', { exact: true })).toBeVisible();
  await expect(page.getByText('read preferences', { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.html) }))).toEqual([]);
  await page.getByRole('button', { name: 'Oak', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Oak Street is your preference.', { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.html) }))).toEqual([]);
});

test('cards show availability, rental period, author and correspondence without opening details', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await page.getByRole('button', { name: 'Favorite Sunlit home in Lincoln Park', exact: true }).click();
  execFileSync('python', ['-c', `import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from store import ListingStore; store=ListingStore(sys.argv[2]); data=json.loads(Path(sys.argv[1]).parent.joinpath('sample.json').read_text())[0]; data.pop('rank'); data.pop('reason'); data.update(status='unavailable',rental_period='week'); store.upsert_listing(data); store.request_contact(data['id']); store.correspond(data['id'],'incoming','email','The viewing is confirmed.',external_message_id='reply-1'); store.add_note('Check the windows.',listing_id=data['id'],role='agent')`, resolve('../profile/skills/gallery/scripts'), gallery.database]);
  await page.getByRole('tab', { name: 'Favorites' }).click();
  await expect(page.getByRole('article').getByText('Unavailable', { exact: true })).toBeVisible();
  await expect(page.getByRole('article').getByText('/ week', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Contacted' }).click();
  await expect(page.getByRole('article').getByText('The viewing is confirmed.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByRole('dialog').getByText(/^Hermes ·/)).toBeVisible();
});

test('background revision checks do not spin Refresh or reload an unchanged page', async ({ page, gallery }) => {
  await page.clock.install();
  let reads = 0;
  page.on('request', request => { if (request.url().includes('/api/listings?')) reads++; });
  await page.goto(gallery.url);
  await expect(page.getByRole('article')).toHaveCount(4);
  const before = reads;
  await page.clock.fastForward(16000);
  await expect(page.getByRole('button', { name: 'Refresh listings' })).not.toHaveAttribute('data-loading');
  expect(reads).toBe(before);
  const focusedRevision = page.waitForResponse(response => response.url().endsWith('/api/revision'));
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    window.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    window.dispatchEvent(new Event('visibilitychange'));
  });
  await focusedRevision;
  expect(reads).toBe(before);
});

test('the launch URL authenticates API requests and removes its secret from the address bar', async ({ page, gallery, request }) => {
  const base = new URL(gallery.url).origin;
  expect((await request.get(base + '/api/health')).status()).toBe(401);
  expect((await request.post(base + '/api/chat', { data: { text: 'Unauthorized', request_id: 'blocked' } })).status()).toBe(401);
  await page.goto(base);
  await expect(page.getByRole('alert')).toContainText('access');
  await page.goto(gallery.url);
  await expect(page.getByRole('article')).toHaveCount(4);
  expect(new URL(page.url()).hash).toBe('');
});

test('approval choices use human labels and send only the selected gateway value', async ({ page, gallery }) => {
  let answered = false;
  await page.route('**/api/chat**', async route => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON().answer).toBe('deny');
      answered = true;
    }
    await route.fulfill({ json: { active: !answered, total: 1, messages: [{ id: 1, role: 'agent', events: [],
      content: answered ? 'The action was denied.' : 'This action needs approval.', status: answered ? 'complete' : 'running',
      prompt: answered ? null : { prompt_id: 'approval', kind: 'approval', description: 'Allow this test action?', choices: ['once', 'session', 'deny'] },
    }] } });
  });
  await page.goto(gallery.url);
  await page.getByRole('tab', { name: 'Notes' }).click();
  await expect(page.getByRole('button', { name: 'Allow once', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Allow for this conversation', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Deny', exact: true }).click();
  await expect(page.getByText('The action was denied.', { exact: true })).toBeVisible();
});

test('partial unranked listings are visible and a failed contact can be retried', async ({ page, gallery }) => {
  execFileSync('python', ['-c', `import sys; sys.path.insert(0,sys.argv[1]); from store import ListingStore; store=ListingStore(sys.argv[2]); store.upsert_listing({'id':'partial','title':'New match','price':'Price on request','sources':[{'label':'Source','url':'https://example.com/new'}]}); store.request_contact('partial'); store.set_contact('partial','failed',channel='email',error='Delivery failed.')`, resolve('../profile/skills/gallery/scripts'), gallery.database]);
  await page.goto(gallery.url);
  await expect(page.getByRole('heading', { name: 'Awaiting ranking', exact: true })).toBeVisible();
  await expect(page.getByRole('article').filter({ hasText: 'New match' }).getByText('Price on request', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View New match', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('Delivery failed.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Retry contact', exact: true }).click();
  await expect(page.getByRole('dialog').getByText('Request saved. No message has been sent yet.')).toBeVisible();
  await expect(page.getByRole('dialog').getByText('No correspondence yet. Hermes sends the first message within a few minutes while its gateway runs.', { exact: true })).toBeVisible();
});

test('a listing read failure is not reported as a save failure', async ({ page, gallery }) => {
  await page.goto(gallery.url);
  await expect(page.getByRole('article')).toHaveCount(4);
  await page.route('**/api/listings/sample*', route => route.abort());
  await page.getByRole('button', { name: 'View Sunlit home in Lincoln Park', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Could not load listing');
  await expect(page.getByRole('dialog').getByText('Could not save', { exact: true })).toHaveCount(0);
});
