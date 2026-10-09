import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
function classes() {
  const values = new Set();
  return { contains: (value) => values.has(value), add: (value) => values.add(value), remove: (value) => values.delete(value), toggle(value, active) { active ? values.add(value) : values.delete(value); } };
}
const sidebar = { classList: classes() };
const dashboard = { classList: classes() };
const exit = { hidden: true };
globalThis.snEditingFixture = { sidebar, dashboard };
globalThis.document = { body: { classList: classes() }, querySelector: () => exit };
let source = await readFile(new URL('../assets/public/editing-controls.js',import.meta.url),'utf8');
source = source.replace(/^import .*;\n/gm,'const {sidebar,dashboard}=globalThis.snEditingFixture;\n');
const {syncEditingControls} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('navigation and page editing share one exit and restore collapsed sidebar only on exit', () => {
  dashboard.classList.add('is-collapsed');
  sidebar.classList.add('is-editing');
  syncEditingControls();
  assert.equal(exit.hidden,false);
  assert.equal(document.body.classList.contains('sn-editing'),true);
  assert.equal(dashboard.classList.contains('is-collapsed'),false);
  document.body.classList.add('sn-page-canvas-editing');
  sidebar.classList.remove('is-editing');
  syncEditingControls();
  assert.equal(exit.hidden,false);
  assert.equal(dashboard.classList.contains('is-collapsed'),false);
  document.body.classList.remove('sn-page-canvas-editing');
  syncEditingControls();
  assert.equal(exit.hidden,true);
  assert.equal(document.body.classList.contains('sn-editing'),false);
  assert.equal(dashboard.classList.contains('is-collapsed'),true);
});
