import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';

let list;
const opened = [];
const pending = [];
const detach = () => { for (const row of list?.rows || []) row.em.isConnected = false; };
globalThis.snInteractionFixture = {
  data: {}, search: null,
  modalContent: { querySelector: () => list },
  serviceName: (service) => service.name,
  escapeHtml: (value) => value,
  showModal: () => { detach(); list = { rows: [], append(row) { this.rows.push(row); row.em.isConnected = true; } }; },
  closeModal: () => { detach(); list = null; },
};
globalThis.document = { createElement() {
  const row = { em: { isConnected: false }, strong: {}, small: {}, button: { addEventListener(type, action) { this.click = action; } } };
  Object.defineProperty(row, 'innerHTML', { set() { row.em.className = 'detecting'; row.em.textContent = '检测中…'; } });
  row.querySelector = (selector) => ({ em: row.em, strong: row.strong, small: row.small, button: row.button })[selector];
  return row;
} };
globalThis.window = { open: (url) => opened.push(url) };
globalThis.location = { protocol: 'http:' };
globalThis.fetch = (url) => new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
let source = await readFile(new URL('../assets/public/service-interactions.js', import.meta.url), 'utf8');
source = source.replace(/^import .*;\n/gm, '');
source = 'const {data,modalContent,search,escapeHtml,serviceName,closeModal,showModal}=globalThis.snInteractionFixture;\n' + source;
const { openItem } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const item = { name: 'HTTP group', serviceType: 'http', services: [
  { id: 'a', name: 'A', url: 'http://localhost/a' },
  { id: 'b', name: 'B', url: 'http://localhost/b' },
] };
const flush = () => new Promise((resolve) => setImmediate(resolve));

test('dialog opens before probes and updates each result independently', async () => {
  const completion = openItem(item);
  assert.equal(list.rows.length, 2);
  assert.deepEqual(list.rows.map((row) => row.em.textContent), ['检测中…', '检测中…']);
  const requests = pending.splice(0);
  requests[0].resolve({}); await flush();
  assert.deepEqual(list.rows.map((row) => row.em.textContent), ['可达', '检测中…']);
  requests[1].reject(new Error('unreachable')); await completion;
  assert.equal(list.rows[1].em.textContent, '不可达');
});

test('open button works during probes and late results do not reopen or update a replaced dialog', async () => {
  const first = openItem(item);
  const oldRows = list.rows;
  const oldRequests = pending.splice(0);
  list.rows[0].button.click();
  assert.equal(opened.at(-1), 'http://localhost/a');
  assert.equal(list, null);
  const second = openItem(item);
  const newRows = list.rows;
  const newRequests = pending.splice(0);
  oldRequests.forEach((request) => request.resolve({})); await first;
  assert.equal(list.rows, newRows);
  assert.equal(oldRows[0].em.textContent, '检测中…');
  assert.equal(newRows[0].em.textContent, '检测中…');
  snInteractionFixture.closeModal();
  newRequests.forEach((request) => request.resolve({})); await second;
  assert.equal(list, null);
});

test('single HTTP service still opens directly without probing', async () => {
  await openItem({ ...item, services: [item.services[0]] });
  assert.equal(opened.at(-1), 'http://localhost/a');
  assert.equal(pending.length, 0);
  assert.equal(list, null);
});
