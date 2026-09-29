import { cp, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const vendor = resolve(root, 'assets/vendor');
await mkdir(vendor, {recursive: true});
for (const [from, to] of [
  ['node_modules/github-markdown-css/github-markdown.css', 'github-markdown.css'],
  ['node_modules/katex/dist/katex.min.css', 'katex.min.css'],
  ['node_modules/katex/dist/fonts', 'fonts'],
  ['node_modules/highlight.js/styles/github.css', 'highlight.css'],
  ['node_modules/mermaid/dist/mermaid.esm.min.mjs', 'mermaid.esm.min.mjs'],
]) await cp(resolve(root, from), resolve(vendor, to), {recursive: true});
