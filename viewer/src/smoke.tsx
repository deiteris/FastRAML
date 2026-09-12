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
import {
  Index,
  camel,
  declarations,
  facetsOf,
  isRecursive,
  isRef,
  labelOf,
  type Document,
  type Shape,
} from './model';
import { RATIO_FACETS, rationalOf, showRatio } from './rational';

const source = process.argv[2] ?? 'public/api.json';
const document = JSON.parse(readFileSync(source, 'utf-8')) as Document;
const index = new Index(document);

/**
 * The addresses of the security schemes, which the checks below are not about.
 *
 * A scheme carries `id`, `name` and `type` -- the three keys that identify a
 * shape -- so a structural walk visits one and reports its `settings` and
 * `described_by` as facets of a type. Told apart by address rather than by
 * guessing from its keys, because the document lists them.
 * `tests/tck/test_properties.py` excludes them from law 19 for the same reason.
 */
const SCHEMES: ReadonlySet<string> = new Set(
  declarations(document.security_schemes)
    .map(({ value }) => value.id)
    .filter((id): id is string => id !== null),
);

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
 * Every name that hides something offers the control that shows it.
 *
 * The bug this exists for was silent in the strongest sense: a property typed
 * by a named `string` with a `pattern` rendered as a link and a full stop. The
 * constraint was written, decoded by every pass, emitted into the tree, and
 * shown in no place a reader of that property would look -- and the page it was
 * missing from rendered, type-checked and screenshotted without complaint,
 * because a page that omits something looks exactly like a type that says
 * nothing.
 *
 * Counted rather than located, because a page renders in its collapsed state
 * and a count is what is observable from outside. `>=`, not `==`: an inline
 * object contributes controls of its own, and this is a floor.
 */
let expected = 0;
for (const { file, name, value } of declarations(document.types)) {
  const want = controls(value);
  expected += want;
  if (want === 0) continue;
  const html = renderToStaticMarkup(
    <MemoryRouter initialEntries={[at('types', file, name)]}>
      <Pages document={document} index={index} />
    </MemoryRouter>,
  );
  const got = (html.match(/class="expander"/g) ?? []).length;
  if (got < want) {
    process.stderr.write(`HIDDEN ${name}: ${want} attributes lead somewhere, ${got} controls to get there\n`);
    failed += 1;
  }
}
if (expected === 0) {
  process.stderr.write('no declared type has an attribute that leads anywhere; the check above is vacuous\n');
  failed += 1;
}
process.stdout.write(`${expected} attributes lead somewhere, all reachable\n`);

/** How many of a type's own attributes keep what they say behind a control. */
function controls(shape: Shape): number {
  const rows = [...Object.values(shape.properties ?? {}), ...Object.values(shape.pattern_properties ?? {})];
  return rows.filter(({ type }) => {
    if (type === null || type === undefined) return false;
    // A name declared elsewhere: the page it names is not this page.
    if (isRef(type)) return says(index.shape(type.$ref));
    if (isRecursive(type)) return false;
    // An array declared here, whose *items* are the thing with something to say.
    if (type.type !== 'array') return false;
    const items = type.items;
    return isRef(items) ? says(index.shape(items.$ref)) : !isRecursive(items) && says(items);
  }).length;
}

/**
 * Whether a shape carries anything at all -- read off the JSON's keys.
 *
 * Deliberately **not** `detailed`. Asking the view's own predicate what the
 * view should render is a check that can only agree with itself: narrowing
 * `detailed` back to "has properties", which is the bug, narrows both sides
 * equally and the count still matches. Falsified exactly that way, and it
 * passed.
 *
 * So the expectation comes from the tree instead. Every key below is something
 * the row that names the shape already shows -- its name, its type, its prose,
 * whether it is required -- and a shape with nothing else is a shape a control
 * would open onto blank space. Anything else is content with one place to be.
 */
function says(shape: Shape | undefined): boolean {
  if (!shape) return false;
  const ALREADY_ON_THE_ROW = new Set([
    'id',
    'name',
    'type',
    'type_expr',
    'display_name',
    'description',
    'required',
    'inherits',
    'annotations',
    'head',
    'discriminator_value',
    'projection',
  ]);
  return Object.keys(shape).some((key) => !ALREADY_ON_THE_ROW.has(key));
}

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

/*
 * `facetsOf` is a deny-list, so a key the tree gains is a facet chip by default.
 *
 * That is the wrong default and cannot be changed here -- the view has no way
 * to know which of a shape's keys are a kind's facets. The generated contract
 * does: `tree.d.ts` groups them under one comment, written by the generator
 * from the kind classes. Reading it back is what makes the deny-list a claim
 * that can be false rather than a list that quietly rots.
 *
 * `projection` and `json_schema` arrived as chips holding a whole JSON Schema
 * before this existed.
 */
const contract = readFileSync('src/tree.d.ts', 'utf-8');
const facetSection = contract.slice(contract.indexOf('/* Facets, by the kind that declares each. */'));
const declaredFacets = new Set(
  [...facetSection.slice(0, facetSection.indexOf('\n}')).matchAll(/^\s{2}(\w+)\??:/gm)].map((m) => camel(m[1]!)),
);
if (declaredFacets.size < 10) {
  process.stderr.write(`only ${declaredFacets.size} facets read from tree.d.ts; the check below is vacuous\n`);
  failed += 1;
}
const stray = new Set<string>();
walk(document, (shape) => {
  for (const [name] of facetsOf(shape)) if (!declaredFacets.has(name)) stray.add(name);
});
for (const name of stray) {
  process.stderr.write(`FACET  ${name} is rendered as a facet and is not one; add it to NOT_A_FACET\n`);
  failed += 1;
}
process.stdout.write(`${declaredFacets.size} facets declared, none stray\n`);
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
    const shaped = 'id' in record && 'name' in record && 'type' in record;
    if (shaped && !SCHEMES.has(record.id as string)) visit(node as Shape);
    for (const value of Object.values(record)) walk(value, visit);
  }
}

