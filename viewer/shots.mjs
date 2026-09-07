/**
 * Screenshot the app, so layout is looked at rather than reasoned about.
 *
 * Every visual bug found in this app so far -- a caret too small to read as a
 * control, a description printed twice, a union member carrying its container's
 * name -- was invisible in the DOM and obvious in a picture. `npm run smoke`
 * proves a page renders; this is how it is seen.
 *
 *     npm run shots            # writes shots/*.png, both themes
 *     npm run shots -- --dark  # dark only
 *
 * Output is gitignored: these are for looking at, not for diffing.
 */

import { spawn } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import puppeteer from 'puppeteer';

const PORT = 4317;
const PAGES = [
  ['overview', '/'],
  ['types', '/types'],
  ['type-object', '/types/api.raml/Book'],
  ['type-union', '/types/api.raml/Search'],
  ['type-recursive', '/types/api.raml/Chain'],
  ['type-inline-items', '/types/api.raml/Shelf'],
  ['type-union-mixed', '/types/api.raml/Payload'],
  ['type-examples', '/types/api.raml/Review'],
  ['resource', '/endpoints/%2Fbooks'],
  ['operation-get', '/endpoints/%2Fbooks%2F%7Bisbn%7D/get'],
  ['operation-post', '/endpoints/%2Fbooks/post'],
  ['operation-optional-auth', '/endpoints/%2Fbooks%2F%7Bisbn%7D/get'],
  ['operation-search', '/endpoints/%2Fsearch/get'],
  ['operation-responses', '/endpoints/%2Fshelves/post'],
  ['security', '/security/api.raml/oauth2'],
  ['annotation-type', '/annotation-types/api.raml/deprecated'],
];

const only = process.argv.includes('--dark') ? ['dark'] : process.argv.includes('--light') ? ['light'] : ['light', 'dark'];

const server = spawn('npx', ['vite', 'preview', '--port', String(PORT), '--strictPort'], {
  stdio: 'ignore',
  shell: true,
});

try {
  await waitFor(`http://localhost:${PORT}/`);
  rmSync('shots', { recursive: true, force: true });
  mkdirSync('shots', { recursive: true });

  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 1000, deviceScaleFactor: 2 });

  /*
   * Browser failures, reported by name.
   *
   * `npm run smoke` renders to static markup, which does not run the client:
   * an icon package that resolved a second copy of React threw `Invalid hook
   * call` on every page and the smoke run still said 38/38. This caught it, but
   * as a selector timeout with nothing about the cause in it.
   */
  const failures = [];
  const firstLine = (text) => String(text).split(/\r?\n/)[0];
  page.on('pageerror', (error) => failures.push(`${route}: ${firstLine(error.message)}`));
  page.on('console', (message) => {
    if (message.type() === 'error') failures.push(`${route}: ${firstLine(message.text())}`);
  });
  let route = '(startup)';

  for (const theme of only) {
    await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: theme }]);
    for (const [name, at] of PAGES) {
      route = at;
      await page.goto(`http://localhost:${PORT}/#${at}`, { waitUntil: 'networkidle0' });
      // The document loads after the first paint, so wait for content rather
      // than for the network: a screenshot of the loading state proves nothing.
      await page.waitForSelector('main article, main .empty', { timeout: 5000 });
      const file = `shots/${name}${only.length > 1 ? `-${theme}` : ''}.png`;
      await page.screenshot({ path: file, fullPage: true });
      process.stdout.write(`${file}\n`);
    }
  }
  await browser.close();

  if (failures.length > 0) {
    for (const failure of [...new Set(failures)]) console.error('ERROR', failure);
    process.exitCode = 1;
  }
} finally {
  server.kill();
}

async function waitFor(url) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`${url} never came up`);
}
