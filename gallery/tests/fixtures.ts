import { test as base } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';

const script = resolve('../profile/skills/gallery/scripts/gallery.py');
export const test = base.extend<{ gallery: { url: string; restart: () => Promise<string>; database: string } }>({
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

