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
import { fileURLToPath } from 'node:url';
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

/*
 * The dev server, not `vite preview`, though `npm run shots` builds first and
 * so still gates on the build.
 *
 * A production build strips React's development warnings, and those are the
 * only thing that reports invalid DOM: a path branch nested `<li>` inside
 * `<li>`, with `tsc`, `smoke` and this all green, because static rendering
 * does not validate nesting either.
 *
 * Spawned as `node <vite bin>` and not through a shell, because `kill()` on a
 * shell kills the shell. The orphan kept the port, `--strictPort` made the
 * next run's server exit rather than move, and `waitFor` found the orphan and
 * said the server was up -- so a run screenshotted a build made hours before
 * the code it claimed to be checking.
 */
const vite = fileURLToPath(new URL('node_modules/vite/bin/vite.js', import.meta.url));
const server = spawn(process.execPath, [vite, '--port', String(PORT), '--strictPort'], { stdio: 'ignore' });
let exited = null;
server.on('exit', (code) => {
  exited = code;
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
  const pending = [];
  const firstLine = (text) => String(text).split(/\r?\n/)[0];
  page.on('pageerror', (error) => failures.push(`${route}: ${firstLine(error.message)}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    // React's warnings are `console.error(format, ...args)`, and `text()`
    // hands back the format string with its `%s` unfilled -- "%s cannot be a
    // descendant of <%s>" names neither element, which is the whole content of
    // the report. Resolving the arguments is async, so the promise is held and
    // awaited before anything is printed.
    const at = route;
    pending.push(
      Promise.all(message.args().map((argument) => argument.jsonValue().catch(() => null)))
        .then((values) => failures.push(`${at}: ${firstLine(interpolate(values))}`))
        .catch(() => failures.push(`${at}: ${firstLine(message.text())}`)),
    );
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
  await Promise.all(pending);

  if (failures.length > 0) {
    for (const failure of [...new Set(failures)]) console.error('ERROR', failure);
    process.exitCode = 1;
  }
} finally {
  server.kill();
}

/** `console.error(format, ...rest)` as one line: `%s`, `%d`, `%o` and `%i`. */
function interpolate([format, ...rest]) {
  if (typeof format !== 'string') return [format, ...rest].map(String).join(' ');
  let next = 0;
  const filled = format.replace(/%[sdoOif]/g, () => (next < rest.length ? String(rest[next++]) : '%s'));
  return [filled, ...rest.slice(next)].join(' ');
}

async function waitFor(url) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    // Our server, or nothing. A reply from a port we did not open means
    // something else is answering, and shooting that is worse than failing.
    if (exited !== null) throw new Error(`the dev server exited (${exited}); is ${PORT} already in use?`);
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
