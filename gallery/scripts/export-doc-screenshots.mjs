import { copyFileSync, existsSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

const output = resolve('.gallery-test/docs-screenshots');
const directories = readdirSync(output, { withFileTypes: true })
  .filter(entry => entry.isDirectory()).map(entry => resolve(output, entry.name));
const names = ['rerank', 'rerank-review', 'explanation']
  .flatMap(name => ['desktop', 'mobile'].map(viewport => `${name}-${viewport}.png`));
const sources = names.map(name => {
  const matches = directories.map(directory => resolve(directory, name)).filter(existsSync);
  if (matches.length !== 1) throw new Error(`Expected one ${name} screenshot, found ${matches.length}.`);
  return matches[0];
});
for (const [index, name] of names.entries()) {
  copyFileSync(sources[index], resolve('../docs/images', name));
}
console.info(`Exported ${names.length} documentation screenshots.`);
