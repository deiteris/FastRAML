/**
 * The module layering, asserted over the import graph.
 *
 * `pyraml` does this to itself -- `tests/unit/test_views.py` fails if anything
 * under `parser/` imports a view -- and for the same reason: a layer nobody
 * checks is a layer that erodes one convenient import at a time. This app was
 * two files of six hundred lines before it was split, and nothing but habit
 * would have stopped it becoming that again.
 *
 * Three rules:
 *
 *   1. A component never imports a page. Pages compose components; the arrow
 *      points one way or the split means nothing.
 *   2. No import cycle. The one cycle that is real -- a shape holds attributes,
 *      an attribute holds a shape -- lives inside `components/Shape.tsx`, where
 *      a cycle costs nothing, rather than across two modules.
 *   3. `model.ts` and `load.ts` import no component. They are the reading layer
 *      and know nothing about rendering.
 *
 *     node layers.mjs
 */

import { readdirSync, readFileSync } from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';

const ROOT = 'src';
const files = [...walk(ROOT)].filter((path) => /\.tsx?$/.test(path) && !path.endsWith('.d.ts'));
const imports = new Map(files.map((path) => [path, local(path)]));

const failures = [];

for (const [path, targets] of imports) {
  const where = area(path);
  for (const target of targets) {
    if (where === 'components' && area(target) === 'pages') {
      failures.push(`${path} imports the page ${target}: components are composed by pages, not the reverse`);
    }
    if ((path === 'src/model.ts' || path === 'src/load.ts') && area(target) !== null) {
      failures.push(`${path} imports ${target}: the reading layer knows nothing about rendering`);
    }
  }
}

for (const cycle of cycles()) {
  failures.push(`import cycle: ${cycle.join(' -> ')}`);
}

if (failures.length > 0) {
  for (const failure of failures) console.error('LAYER', failure);
  process.exit(1);
}
process.stdout.write(`${files.length} modules, layering holds\n`);

/** Every module this one imports, by repo path; non-relative imports are dropped. */
function local(path) {
  const source = readFileSync(path, 'utf-8');
  const found = [];
  for (const match of source.matchAll(/from\s+'(\.[^']*)'/g)) {
    const target = resolve(dirname(path), match[1]);
    const asPath = relative(process.cwd(), target).split('\\').join('/');
    // A directory import resolves to its `index.ts`, and an extensionless one
    // to whichever extension exists.
    const candidates = [`${asPath}.ts`, `${asPath}.tsx`, `${asPath}/index.ts`, asPath];
    const hit = candidates.find((one) => files.includes(one));
    if (hit) found.push(hit);
  }
  return found;
}

function area(path) {
  if (path.startsWith('src/components/')) return 'components';
  if (path.startsWith('src/pages/')) return 'pages';
  return null;
}

/** Every cycle, by depth-first search over the import graph. */
function cycles() {
  const found = [];
  const state = new Map();
  const walkFrom = (node, stack) => {
    if (state.get(node) === 'done') return;
    const at = stack.indexOf(node);
    if (at !== -1) {
      found.push([...stack.slice(at), node]);
      return;
    }
    stack.push(node);
    for (const next of imports.get(node) ?? []) walkFrom(next, stack);
    stack.pop();
    state.set(node, 'done');
  };
  for (const node of imports.keys()) walkFrom(node, []);
  return found;
}

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name).split('\\').join('/');
    if (entry.isDirectory()) yield* walk(path);
    else yield path;
  }
}
