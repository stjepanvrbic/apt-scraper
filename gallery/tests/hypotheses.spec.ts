import { expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test } from './fixtures';

test('Rankings marks guesses and confirms or rejects them without moving listings', async ({ page, gallery }, testInfo) => {
  await page.route('https://photos.example.com/**', route => route.fulfill({ path: resolve('public/media/living.jpg'), contentType: 'image/jpeg' }));
  execFileSync('python', ['-c', `import sys
sys.path.insert(0, sys.argv[1])
from ranking_hypothesis_experiment import seed_hypotheses, ListingStore
from hypotheses import assess
store = ListingStore(sys.argv[2])
seed_hypotheses(store, count=30)
assess(store)`, resolve('../tests'), gallery.database]);
  await page.goto(gallery.url);
  const guesses = page.getByRole('region', { name: 'Preference guesses' });
  await expect(guesses).toContainText("the model's guesses");
  await expect(guesses).toContainText('Still learning about: white kitchen cabinet doors');
  await expect(guesses.getByRole('button', { name: 'Confirm preference: white kitchen cabinet doors', exact: true })).toBeDisabled();
  await expect(page.getByText('Learning from 2 labels', { exact: true })).toBeVisible();
  const ranks = await page.locator('.rank-badge').allTextContents();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('hypotheses-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await guesses.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('hypotheses-mobile.png') });
  await guesses.getByRole('button', { name: 'Confirm preference: original wood trim', exact: true }).click();
  await expect(guesses.getByRole('button', { name: 'Confirm preference: original wood trim', exact: true })).toHaveCount(0);
  await guesses.getByRole('button', { name: 'Reject guess: hand painted tile', exact: true }).click();
  await expect(guesses.getByRole('button', { name: 'Reject guess: hand painted tile', exact: true })).toHaveCount(0);
  await expect.poll(() => page.locator('.rank-badge').allTextContents()).toEqual(ranks);
  const preferences = JSON.parse(execFileSync('python', ['-c', `import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); print(c.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()[0])`, gallery.database], { encoding: 'utf8' }));
  expect(preferences.likes).toContain('original wood trim');
  expect(preferences.likes).not.toContain('hand painted tile');
});
