import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react(), {
    name: 'normalize-output-endings',
    writeBundle(options) {
      for (const name of ['index.html', 'licenses.txt', 'media/font-license.txt']) {
        const path = resolve(options.dir!, name);
        writeFileSync(path, readFileSync(path, 'utf8').replace(/\r\n?/g, '\n'));
      }
    },
  }],
  build: {
    outDir: '../skills/gallery/web', emptyOutDir: true, license: { fileName: 'licenses.txt' },
    rolldownOptions: { output: {
      entryFileNames: 'assets/gallery.js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]',
    } },
  },
});
