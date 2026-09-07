/**
 * Render every page of a document, and fail on the first one that throws.
 *
 * `tsc` says the components type-check, which is not the same as saying they
 * render: an index that misses, a facet whose value is an object where a string
 * was assumed, a recursion marker read as a link -- all compile.
 *
 * This is the JavaScript half of law 13 (docs/14 § 4): every declared type and
 * every endpoint has a view. It walks the document's own contents rather than a
 * fixed route list, so a construct the sample gains is covered without this file
 * changing.
 *
 *     npm run smoke -- public/api.json
 */

import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { Pages } from './App';
import { renderMarkdown } from './components/markdown';
import { Index, declarations, facetsOf, isRef, labelOf, type Document, type Shape } from './model';
import { RATIO_FACETS, rationalOf, showRatio } from './rational';

const source = process.argv[2] ?? 'public/api.json';
const document = JSON.parse(readFileSync(source, 'utf-8')) as Document;
const index = new Index(document);

const routes = [
  '/',
  '/types',
  '/annotation-types',
  '/security',
  '/documentation',
  '/nonsense',
  // Every documentation item, by the position its route is keyed on. An item
  // is the one thing in the tree with no address, so this is the only check
  // that the numbering the nav writes and the numbering the page reads agree.
  ...(document.entry_point?.documentation ?? []).map((_, at) => `/documentation/${at}`),
  ...Object.keys(document.endpoints).map((path) => `/endpoints/${encodeURIComponent(path)}`),
  ...declarations(document.types).map(({ file, name }) => at('types', file, name)),
  ...declarations(document.annotation_types).map(({ file, name }) => at('annotation-types', file, name)),
  ...declarations(document.security_schemes).map(({ file, name }) => at('security', file, name)),
  ...[...index.byAddress.keys()].map((address) => `/n/${encodeURIComponent(address)}`),
];

function at(section: string, file: string, name: string): string {
  return `/${section}/${encodeURIComponent(file)}/${encodeURIComponent(name)}`;
}

let failed = 0;
let smallest = { route: '', size: Number.POSITIVE_INFINITY };
for (const route of routes) {
  try {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={[route]}>
        <Pages document={document} index={index} />
      </MemoryRouter>,
    );
    // An empty render is the quiet failure: a page that resolved nothing looks
    // exactly like one whose document said nothing.
    if (html.length < smallest.size) smallest = { route, size: html.length };
    if (html.length < 40) {
      process.stderr.write(`EMPTY  ${route}\n`);
      failed += 1;
    }
  } catch (error) {
    process.stderr.write(`THROWS ${route}\n       ${String(error).split('\n')[0]}\n`);
    failed += 1;
  }
}

process.stdout.write(
  `${routes.length - failed}/${routes.length} routes rendered ` +
    `(smallest ${smallest.size} bytes at ${smallest.route})\n`,
);

/*
 * Two things rendering cannot tell you, because both produce a page that looks
 * fine and says something untrue.
 */

let shapes = 0;
let members = 0;
walk(document, (shape) => {
  shapes += 1;

  // A union member must be named by its own `type`, its ref target, or what
  // repeats -- never by `type_expr`, which P7 stamps with the whole expression
  // every member was built from.
  //
  // Tested by what a borrowed expression looks like -- a union operator in the
  // name of one member -- and not by comparing the member's label against a
  // second expression written here. Comparing against the *parent's* is what
  // this asked first, and it passed on a query parameter typed `Search`: the
  // parent read `Search`, the members read `string | number`, the two differed,
  // and both members were still wrong. Comparing against a re-derivation of
  // `labelOf` is worse, because it can only agree with itself.
  for (const member of shape.any_of ?? []) {
    members += 1;
    const label = labelOf(member, index);
    if (label.includes('|')) {
      process.stderr.write(`UNION  ${shape.name ?? shape.id}: member reads "${label}", the whole union's expression\n`);
      failed += 1;
    }
  }

  // The JavaScript form of law 15. A `$ref` to something the index does not
  // hold renders as "unresolved", which a reader cannot tell from a document
  // that genuinely pointed nowhere.
  for (const node of [shape.items, ...(shape.inherits ?? []), ...(shape.any_of ?? [])]) {
    if (isRef(node) && !index.get(node.$ref)) {
      process.stderr.write(`DANGLE ${shape.name ?? shape.id}: ${node.$ref}\n`);
      failed += 1;
    }
  }
});

process.stdout.write(`${shapes} shapes checked, ${members} union members\n`);

/*
 * The Markdown renderer, on a description written to attack the reader.
 *
 * Descriptions are Markdown by the spec and are rendered as Markdown, which
 * puts author-controlled text through `dangerouslySetInnerHTML`. The safety of
 * that rests on two settings, and a setting is exactly the kind of thing a
 * later edit turns off while every page still renders.
 */
const HOSTILE: [string, string][] = [
  ['a raw script tag', '<script>alert(1)</script>'],
  ['an inline event handler', '<img src=x onerror="alert(1)">'],
  ['an iframe', '<iframe src="https://example.com"></iframe>'],
  ['a javascript: link', '[click](javascript:alert(1))'],
  ['a javascript: link, cased', '[click](JaVaScRiPt:alert(1))'],
  ['a data: link', '[click](data:text/html;base64,PHNjcmlwdD4=)'],
  ['an autolink', '<javascript:alert(1)>'],
  ['a reference link', '[click][evil]\n\n[evil]: javascript:alert(1)'],
];
/*
 * Read the *tags*, not the string.
 *
 * `<img src=x onerror="alert(1)">` renders as the escaped text
 * `&lt;img src=x onerror=&quot;...`, which is exactly the right outcome and
 * still contains `onerror=`. Escaped output has no `<` in it at all, so
 * extracting tags first is what separates "rendered dangerously" from
 * "rendered safely, as the words the author typed".
 */
const EXECUTES = /^<\s*(script|iframe|object|embed|img|svg|form)\b|\son\w+\s*=/i;
const BAD_TARGET = /(?:href|src)\s*=\s*["']?\s*(?:javascript|vbscript|data):/i;
for (const [what, source] of HOSTILE) {
  const html = renderMarkdown(source);
  const dangerous = (html.match(/<[^>]*>/g) ?? []).filter((tag) => EXECUTES.test(tag) || BAD_TARGET.test(tag));
  if (dangerous.length > 0) {
    process.stderr.write(`UNSAFE ${what}: ${source}\n       rendered ${dangerous.join(' ')}\n`);
    failed += 1;
  }
}
// An ordinary link still works, and leaves with no handle on this window.
const link = renderMarkdown('[docs](https://example.com)');
if (!link.includes('href="https://example.com"') || !link.includes('rel="noopener noreferrer nofollow"')) {
  process.stderr.write(`LINK   an external link lost its hardening: ${link.trim()}\n`);
  failed += 1;
}
// And that it is rendering at all -- a renderer that returned "" would pass
// every check above.
if (!renderMarkdown('a *list*:\n\n- one\n').includes('<li>')) {
  process.stderr.write('the Markdown renderer produced no list; the checks above are vacuous\n');
  failed += 1;
}
process.stdout.write(`${HOSTILE.length} hostile descriptions checked\n`);

/*
 * The exact ratios, read back as the decimals they were written as.
 *
 * `Number(n) / Number(d)` would pass the first three of these and is the one
 * line that puts the value back through the float the parser spent its effort
 * avoiding -- `11/10` is the case CLAUDE.md names, where `multipleOf: 1.1` must
 * accept `2.2`.
 */
const RATIOS: [string, string][] = [
  ['1/100', '0.01'],
  ['11/10', '1.1'],
  ['5/2', '2.5'],
  ['7', '7'],
  ['0', '0'],
  ['-3/4', '-0.75'],
  ['1/1024', '0.0009765625'],
  // Beyond a double's 53 bits in both directions.
  ['1/10000000000000000000000', '0.0000000000000000000001'],
  ['123456789012345678901/100', '1234567890123456789.01'],
  // Not a terminating decimal, so it stays a ratio rather than becoming a
  // rounded one. Unreachable from a RAML scalar, and a lie if it appeared.
  ['1/3', '1/3'],
];
for (const [ratio, expected] of RATIOS) {
  const shown = showRatio(ratio);
  if (shown !== expected) {
    process.stderr.write(`RATIO  ${ratio} rendered as ${shown}, expected ${expected}\n`);
    failed += 1;
  }
}

/*
 * And that the document really does carry ratios where this claims.
 *
 * `string | number` is what the binding says, and both occur: an integer bound
 * arrives as a JSON number and a `Fraction` as a string. Only the strings are
 * this module's business -- but if none of them were strings any more, the
 * conversion above would be dead code passing its own tests, so one has to be.
 */
let ratios = 0;
walk(document, (shape) => {
  for (const [name, value] of facetsOf(shape)) {
    if (!RATIO_FACETS.has(name) || typeof value !== 'string') continue;
    ratios += 1;
    if (rationalOf(value) === null) {
      process.stderr.write(`RATIO  ${shape.name ?? shape.id}: ${name} is ${JSON.stringify(value)}, not a ratio\n`);
      failed += 1;
    }
  }
});
if (ratios === 0) {
  process.stderr.write('no facet in the document arrived as a ratio; the conversion is untested against real output\n');
  failed += 1;
}
process.stdout.write(`${RATIOS.length} ratios converted, ${ratios} in the document\n`);
if (shapes < 10) {
  process.stderr.write('the document produced almost no shapes; the checks above are vacuous\n');
  failed += 1;
}
process.exit(failed === 0 ? 0 : 1);

/** Every shape in the document, by the three keys `shape()` always writes. */
function walk(node: unknown, visit: (shape: Shape) => void): void {
  if (Array.isArray(node)) {
    for (const item of node) walk(item, visit);
  } else if (typeof node === 'object' && node !== null) {
    const record = node as Record<string, unknown>;
    if ('id' in record && 'name' in record && 'type' in record) visit(node as Shape);
    for (const value of Object.values(record)) walk(value, visit);
  }
}
