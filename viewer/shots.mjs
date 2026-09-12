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
  ['documentation', '/documentation'],
  ['documentation-item', '/documentation/1'],
  ['types', '/types'],
  ['type-object', '/types/sample%2Fapi.raml/Book'],
  ['type-union', '/types/sample%2Fapi.raml/Search'],
  ['type-union-large', '/types/sample%2Fapi.raml/Anything'],
  ['type-union-referenced', '/types/sample%2Fapi.raml/Paged'],
  ['type-scalar', '/types/sample%2Fapi.raml/Isbn'],
  ['type-from-shared-library', '/types/shared%2Fmeasures.raml/Parcel'],
  ['type-at-the-limits', '/types/shared%2Fmeasures.raml/Tolerance'],
  ['type-recursive', '/types/sample%2Fapi.raml/Chain'],
  ['type-inline-items', '/types/sample%2Fapi.raml/Shelf'],
  ['type-union-mixed', '/types/sample%2Fapi.raml/Payload'],
  ['type-examples', '/types/sample%2Fapi.raml/Review'],
  ['type-json-schema', '/types/sample%2Fapi.raml/Invoice'],
  ['type-json-schema-inline', '/types/sample%2Fapi.raml/Barcode'],
  ['type-from-library', '/types/sample%2Fcommon.raml/Address'],
  ['type-using-library', '/types/sample%2Fapi.raml/Delivery'],
  ['operation-library-annotation', '/endpoints/%2Fdeliveries/get'],
  ['type-discriminated', '/types/sample%2Fapi.raml/Publication'],
  ['type-discriminator-value', '/types/sample%2Fapi.raml/Magazine'],
  ['type-discriminator-default', '/types/sample%2Fapi.raml/Pamphlet'],
  ['operation-protocols', '/endpoints/%2Fpublications/get'],
  ['resource', '/endpoints/%2Fbooks'],
  ['operation-get', '/endpoints/%2Fbooks%2F%7Bisbn%7D/get'],
  ['operation-post', '/endpoints/%2Fbooks/post'],
  ['operation-optional-auth', '/endpoints/%2Fbooks%2F%7Bisbn%7D/get'],
  ['operation-search', '/endpoints/%2Fsearch/get'],
  ['operation-responses', '/endpoints/%2Fshelves/post'],
  ['security', '/security/sample%2Fapi.raml/oauth2'],
  ['annotation-type', '/annotation-types/sample%2Fapi.raml/deprecated'],
];

/*
 * Wide, and then narrow over the pages where width is the thing under test.
 *
 * A layout only fails at the width it fails at. A tab strip with ten members, a
 * response selector with six codes and an attribute row carrying a long value
 * all fit at 1400 and none of them fit at 760, so a run at one width says
 * nothing about the other -- and 760 is a window split beside an editor, which
 * is where API documentation is actually read.
 */
const NARROW = new Set([
  'type-union-large',
  'type-union-referenced',
  'operation-responses',
  'operation-post',
  'type-examples',
  'type-at-the-limits',
]);
const VIEWPORTS = [
  { suffix: '', width: 1400 },
  { suffix: '-narrow', width: 760, pages: NARROW },
];

/**
 * The regions that open a level, each of which must look the same everywhere.
 *
 * An item type, an expanded declaration and a union member are all *a separate
 * type inside another one*, and a reader learns one boundary per construct --
 * so a construct drawn with a rule on one page and flush on the next teaches
 * that the rule means something, which it then does not.
 *
 * `each item` was flush on an attribute row and indented everywhere else, which
 * put an array's `maxItems` and its item's `maxLength` in one column under one
 * heading. A tab strip draws its own boundary and needs no rule; what matters
 * is that it needs none consistently.
 */
const NESTINGS = [
  ['each item', '.group > .nested'],
  ['an expanded type', '.expand > .nested'],
  ['a union member', '.tabs-panel'],
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
  /** Per nesting construct, every indent it was drawn at and where. */
  const levels = {};
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
    for (const view of VIEWPORTS) {
      await page.setViewport({ width: view.width, height: 1000, deviceScaleFactor: 2 });
      for (const [name, at] of PAGES) {
        if (view.pages && !view.pages.has(name)) continue;
        route = `${at}${view.suffix}`;
        await page.goto(`http://localhost:${PORT}/#${at}`, { waitUntil: 'networkidle0' });
        // The document loads after the first paint, so wait for content rather
        // than for the network: a screenshot of the loading state proves nothing.
        await page.waitForSelector('main article, main .empty', { timeout: 5000 });
        for (const spill of await overflowing(page)) failures.push(`${route}: ${spill}`);
        const file = `shots/${name}${view.suffix}${only.length > 1 ? `-${theme}` : ''}.png`;
        await page.screenshot({ path: file, fullPage: true });
        process.stdout.write(`${file}\n`);
        // After the shot: this opens every control, and the picture is of the
        // page as a reader first meets it.
        for (const [kind, seen] of await nesting(page)) {
          for (const [step, chain] of seen) (levels[kind] ??= new Map()).set(step, `${route} ${chain}`);
        }
      }
    }
  }
  await browser.close();
  await Promise.all(pending);

  for (const [kind] of NESTINGS) {
    const seen = levels[kind] ?? new Map();
    if (seen.size === 0) {
      failures.push(`no page draws ${kind}; the check on it is vacuous`);
    } else if (seen.size > 1) {
      const where = [...seen].map(([step, at]) => `\n         ${step} at ${at}`).join('');
      failures.push(`${kind} is drawn at ${seen.size} different indents:${where}`);
    }
  }
  process.stdout.write(
    `${NESTINGS.map(([kind]) => `${kind} ${[...(levels[kind] ?? new Map()).keys()][0]}`).join(', ')}\n`,
  );

  if (failures.length > 0) {
    for (const failure of [...new Set(failures)]) console.error('ERROR', failure);
    process.exitCode = 1;
  }
} finally {
  server.kill();
}

/**
 * Anything wider than the column it was laid out in.
 *
 * The one class of bug a screenshot shows and nothing else does: a `pattern:`
 * with no space in it is a single word by every rule the browser has, so it
 * ran out of its container, past the article, past the viewport, and took a
 * horizontal scrollbar with it. `tsc` is happy, the page renders, the markup
 * is correct, and the value is unreadable.
 *
 * Two measurements, because either one alone passes the bug it is there for.
 * A box may sit inside the page and paint its text outside itself -- capping
 * the box with `max-width` does exactly that, and the first version of this
 * check called the result contained. So: a rect that ends past `<main>`, *or*
 * content wider than the box holding it.
 *
 * Only leaves are reported -- an overflowing chip also overflows every ancestor
 * it sits in, and naming all of them buries the one that is too wide. `.code`
 * is exempt: a code block scrolls on purpose.
 *
 * So is a box marked `data-scroll`, and only as narrowly as that means: content
 * it can reach is not content lost, and a child's position is wherever the
 * scroll offset put it. The box itself is still measured against the page -- a
 * scroller wider than the column it sits in is the same bug as any other -- and
 * every child is still measured against its own box.
 *
 * The mark is a claim and the claim is checked. An element that says it scrolls
 * and does not clips its content with no way to reach it, which is exactly the
 * bug being looked for; trusting the attribute would excuse it.
 */
async function overflowing(page) {
  return page.evaluate(() => {
    const main = document.querySelector('main');
    if (!main) return [];
    const limit = main.getBoundingClientRect().right;
    const scrolls = (element) =>
      element.hasAttribute('data-scroll') && ['auto', 'scroll'].includes(getComputedStyle(element).overflowX);
    const within = (element) => {
      for (let at = element.parentElement; at !== null && at !== main; at = at.parentElement) {
        if (scrolls(at)) return true;
      }
      return false;
    };
    const past = (element) => element.getBoundingClientRect().right > limit + 1;
    const spilt = (element) => element.clientWidth > 0 && element.scrollWidth > element.clientWidth + 1;
    const problem = (element) => {
      if (element.closest('.code, pre')) return null;
      if (past(element) && !within(element)) return 'runs past the page';
      if (spilt(element) && !scrolls(element)) return 'is wider than its box';
      return null;
    };
    const spills = [];
    for (const element of main.querySelectorAll('*')) {
      const how = problem(element);
      if (how === null) continue;
      if ([...element.children].some((child) => problem(child) !== null)) continue;
      const name = `${element.tagName.toLowerCase()}${element.className ? `.${String(element.className).split(' ').join('.')}` : ''}`;
      spills.push(`${name} "${(element.textContent ?? '').trim().slice(0, 48)}" ${how}`);
    }
    return [...new Set(spills)];
  });
}


/**
 * Where each construct was drawn on this page, as indent and rule.
 *
 * Every control is opened first, because a region that renders collapsed is a
 * region this cannot measure -- and `.expand > .nested` exists only when open.
 * Three passes, since opening one reveals the next; a recursion marker carries
 * no control, so the descent is finite either way.
 */
async function nesting(page) {
  for (let pass = 0; pass < 3; pass += 1) {
    // One `evaluate` per pass, not one loop inside one: a click schedules a
    // React render, so the controls it reveals are not in the DOM until the
    // call returns and the page has ticked.
    await page.evaluate(() => {
      for (const control of document.querySelectorAll('.expander[aria-expanded="false"]')) control.click();
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return page.evaluate(
    (kinds) => {
      const chain = (element) => {
        const parts = [];
        for (let at = element; at && !at.matches('article'); at = at.parentElement) {
          const own = String(at.className || '')
            .split(' ')
            .filter((name) => ['shape', 'attr', 'group', 'expand', 'nested', 'attributes', 'tabs-panel'].includes(name));
          if (own.length > 0) parts.unshift(own.join('.'));
        }
        return parts.join('>');
      };
      return kinds.map(([kind, selector]) => [
        kind,
        [...document.querySelectorAll(selector)].map((element) => {
          const style = getComputedStyle(element);
          return [`${style.paddingLeft} + ${style.borderLeftWidth} rule`, chain(element)];
        }),
      ]);
    },
    NESTINGS,
  );
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
