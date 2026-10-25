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
    let serverProcess: ChildProcess;
    let address: string;
    const start = async () => {
      serverProcess = spawn('python', [script, '--database', database, '--sample', '--port', '0', 'serve'], {
        env: { ...process.env, USERPROFILE: directory, HOME: directory },
      });
      let errors = '';
      serverProcess.stderr!.on('data', chunk => { errors += chunk; });
      return new Promise<string>((resolve, reject) => {
        const lines = createInterface({ input: serverProcess.stdout! });
        lines.once('line', line => { lines.close(); address = JSON.parse(line).url; resolve(address); });
        serverProcess.once('exit', code => reject(new Error(`Gallery exited ${code}: ${errors}`)));
        serverProcess.once('error', reject);
      });
    };
    const stop = async () => {
      if (serverProcess.exitCode !== null) return;
      const exited = new Promise<void>(resolve => { serverProcess.once('exit', () => resolve()); });
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

