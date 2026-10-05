import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';
const source = await readFile(new URL('../assets/public/theme.js', import.meta.url), 'utf8');
const { analyzePixels, analyzeBackground, initializeTheme, textIconColor, accentTextColor } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const pixel = (r, g, b, alpha = 255) => new Uint8ClampedArray([r, g, b, alpha]);

test('original image luminance, colorful and neutral accents', () => {
  assert.deepEqual(analyzePixels(pixel(255, 255, 255)), { theme: 'light', accent: null });
  assert.deepEqual(analyzePixels(pixel(0, 0, 0)), { theme: 'dark', accent: null });
  assert.equal(analyzePixels(pixel(128, 128, 128)).theme, 'light');
  assert.deepEqual(analyzePixels(pixel(120, 30, 50)), { theme: 'dark', accent: '#781e32' });
  assert.equal(analyzePixels(pixel(255, 0, 0, 0)), null);
  assert.deepEqual(analyzePixels(new Uint8ClampedArray([255, 0, 0, 0, 255, 255, 255, 255])), { theme: 'light', accent: null });
  assert.equal(analyzePixels(new Uint8ClampedArray([120, 30, 50, 255, 120, 30, 50, 255, 40, 140, 200, 255])).accent, '#781e32');
});

test('theme mode uses a CSS reference and preserves fixed colors', () => {
  assert.equal(textIconColor({ iconColorMode: 'theme', iconColor: '#123456' }), 'var(--sn-accent)');
  assert.equal(textIconColor({ iconColor: '#123456' }), '#123456');
});

test('image failure is cached and falls back to system and saved accent', async () => {
  let loads = 0;
  globalThis.Image = class { set src(value) { loads++; queueMicrotask(() => this.onerror()); } };
  assert.equal(await analyzeBackground('/missing.jpg'), null);
  assert.equal(await analyzeBackground('/missing.jpg'), null);
  assert.equal(loads, 1);
  const style = new Map();
  const media = { matches: true, addEventListener(type, listener) { this.listener = listener; } };
  globalThis.window = { matchMedia: () => media };
  globalThis.document = { body: { dataset: {}, style: { setProperty: (key, value) => style.set(key, value) } } };
  initializeTheme({ theme: 'background', accentColorMode: 'background', accentColor: '#123456', backgroundSource: 'custom', backgroundUrl: '/missing.jpg' });
  await Promise.resolve();
  assert.equal(document.body.dataset.snTheme, 'light');
  assert.equal(style.get('--sn-user-accent'), '#123456');
  media.matches = false; media.listener();
  assert.equal(document.body.dataset.snTheme, 'dark');
  initializeTheme({ theme: 'background', accentColorMode: 'background', backgroundSource: 'default' });
  assert.equal(document.body.dataset.snTheme, 'dark');
  assert.equal(style.get('--sn-user-accent'), '#4f7cff');
  initializeTheme({ theme: 'light', accentColor: '#abcdef' });
  media.listener();
  assert.equal(document.body.dataset.snTheme, 'light');
});

test('image is sampled once at a proportional maximum of 64 pixels', async () => {
  let draws = 0;
  const canvas = { getContext: () => ({ drawImage() { draws++; }, getImageData: () => ({ data: pixel(40, 140, 200) }) }) };
  globalThis.document = { createElement: () => canvas };
  globalThis.Image = class {
    naturalWidth = 200; naturalHeight = 100;
    set src(value) { queueMicrotask(() => this.onload()); }
  };
  assert.deepEqual(await analyzeBackground('/color.jpg'), { theme: 'light', accent: '#288cc8' });
  assert.equal(canvas.width, 64); assert.equal(canvas.height, 32);
  await analyzeBackground('/color.jpg'); assert.equal(draws, 1);
});


test('accent foreground follows accent brightness independently of page theme', () => {
  assert.equal(accentTextColor('#352e28'), '#ffffff');
  assert.equal(accentTextColor('#50c8b4'), '#000000');
  assert.equal(accentTextColor('#ffffff'), '#000000');
  assert.equal(accentTextColor('#000000'), '#ffffff');
});
