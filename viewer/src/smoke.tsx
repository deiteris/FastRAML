/**
 * Render every page of a document, and fail on the first one that throws.
 *
 * `tsc` says the components type-check, which is not the same as saying they
 * render: an index that misses, a facet whose value is an object where a string
 * was assumed, a recursion marker read as a link -- all compile.
 *
 * This is the JavaScript half of `TestEveryTypeRenders` in
 * `tests/tck/test_properties.py`: every declared type and every endpoint has a
 * view. It walks the document's own contents rather than a
 * fixed route list, so a construct the sample gains is covered without this file
 * changing.
 *
 *     npm run smoke -- public/api.json
 */

import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { Pages } from './App';
import { highlightCode } from './components/highlighting';
import { renderMarkdown } from './components/markdown';
import {
  Index,
  Tree,
  camel,
  declarations,
  facetsOf,
  isRecursive,
  isRef,
  labelOf,
  type Document,
  type Example,
  type Shape,
} from './model';
import { parse, stringify } from './numbers';

const source = process.argv[2] ?? 'public/api.json';
const document = parse(readFileSync(source, 'utf-8')) as Document;
const index = new Index(Tree.of(document));

/**
 * The addresses of the security schemes, which the checks below are not about.
 *
 * A scheme carries `id`, `name` and `type` -- the three keys that identify a
 * shape -- so a structural walk visits one and reports its `settings` and
 * `described_by` as facets of a type. Told apart by address rather than by
 * guessing from its keys, because the document lists them.
 * `TestNothingArrivesUndeclared` in `tests/tck/test_properties.py` excludes
 * them for the same reason.
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
  for (const member of shape.type === 'union' ? (shape.any_of ?? []) : []) {
    members += 1;
    const label = labelOf(member, index);
    if (label.includes('|')) {
      process.stderr.write(`UNION  ${shape.name ?? shape.id}: member reads "${label}", the whole union's expression\n`);
      failed += 1;
    }
  }

  // The JavaScript form of `TestEveryReferenceResolves` in
  // `tests/unit/test_tree.py`. A `$ref` to something the index does not
  // hold renders as "unresolved", which a reader cannot tell from a document
  // that genuinely pointed nowhere.
  const contained = [
    ...(shape.type === 'array' ? [shape.items] : []),
    ...(shape.type === 'union' ? (shape.any_of ?? []) : []),
  ];
  for (const node of [...contained, ...(shape.inherits ?? [])]) {
    if (isRef(node) && !index.get(node.$ref)) {
      process.stderr.write(`DANGLE ${shape.name ?? shape.id}: ${node.$ref}\n`);
      failed += 1;
    }
  }
});

process.stdout.write(`${shapes} shapes checked, ${members} union members\n`);

/**
 * Everything the row naming a shape already shows.
 *
 * Used two ways: a shape with nothing outside this set says nothing a control
 * could open onto, and a key outside it is a value with one place to be read.
 */
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

/*
 * Nothing a type says is missing from the page that names it.
 *
 * The bug this exists for was silent in the strongest sense: a property typed
 * by a named `string` with a `pattern` rendered as a link and a full stop. The
 * constraint was written, decoded by every pass, emitted into the tree, and
 * shown in no place a reader of that property would look -- and the page it was
 * missing from rendered, type-checked and screenshotted without complaint,
 * because a page that omits something looks exactly like a type that says
 * nothing.
 *
 * Two measurements, because the view has two answers and only one of them is a
 * count:
 *
 * - What a row *names* is declared elsewhere and sits behind a control, so the
 *   controls are counted. A page renders collapsed, so a count is all that is
 *   observable from outside. `>=`, not `==`: an inline object contributes
 *   controls of its own, and this is a floor.
 * - What a row *declares* -- an array whose item type is written out in place --
 *   is shown, so its text must be in the collapsed markup. Located, not
 *   counted: the value itself is the expectation, and a page that drops it
 *   fails by name.
 */
let expected = 0;
let literal = 0;
let borrowed = 0;
for (const { file, name, value } of declarations(document.types)) {
  if (isRef(value)) {
    const target = index.declaration(value);
    if (!target) {
      process.stderr.write(`ALIAS  ${name}: ${value.$ref} has no type page\n`);
      failed += 1;
    }
    continue;
  }
  const want = controls(value);
  const open = declares(value);
  const twice = doubled(value);
  expected += want;
  literal += open.length;
  borrowed += twice.length;
  // Every reason to render, in one condition. Read as two, this skipped a page
  // whose only interest was the third: `Entity` has two scalar properties and
  // no arrays among them, so it left by the `continue` and its `string[]` facet
  // was never drawn. The check passed with the bug on the page.
  if (want === 0 && open.length === 0 && twice.length === 0) continue;
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
  for (const text of open) {
    if (html.includes(escaped(text))) continue;
    process.stderr.write(`LOST ${name}: an item type declares ${JSON.stringify(text)} and the page does not say it\n`);
    failed += 1;
  }
  for (const text of twice) {
    if (!html.includes(escaped(text))) continue;
    process.stderr.write(`TWICE ${name}: an array is named ${JSON.stringify(text)}, its own expression with a suffix\n`);
    failed += 1;
  }
}
if (expected === 0 || literal === 0 || borrowed === 0) {
  process.stderr.write(
    'no declared type has an attribute that leads anywhere, an item type written in place, ' +
      'or an array written as an expression; one of the three checks above is vacuous\n',
  );
  failed += 1;
}
process.stdout.write(
  `${expected} attributes lead somewhere, ${literal} item constraints shown in place, ` +
    `${borrowed} arrays written as an expression\n`,
);

/** How many of a type's own attributes keep what they say behind a control. */
function controls(shape: Shape): number {
  if (shape.type !== 'object') return 0;
  const rows = [...Object.values(shape.properties ?? {}), ...Object.values(shape.pattern_properties ?? {})];
  return rows.filter(({ type }) => {
    if (type === null || type === undefined) return false;
    // A name declared elsewhere: the page it names is not this page.
    if (isRef(type)) return says(index.shape(type.$ref));
    if (isRecursive(type)) return false;
    // An array *naming* its item type: the name is on the row, the declaration
    // is on another page, and one control reaches it.
    if (type.type !== 'array') return false;
    return isRef(type.items) && says(index.shape(type.items.$ref));
  }).length;
}

/**
 * What the item types written inside this declaration say, as literal text.
 *
 * `items: {type: string, pattern: ...}` has no page of its own, so the page
 * naming it is the only place that text can be read. Every value is one the
 * facet chips render verbatim.
 */
function declares(shape: Shape): string[] {
  if (shape.type !== 'object') return [];
  const rows = [...Object.values(shape.properties ?? {}), ...Object.values(shape.pattern_properties ?? {})];
  const said: string[] = [];
  for (const { type } of rows) {
    if (type === null || type === undefined || isRef(type) || isRecursive(type)) continue;
    if (type.type !== 'array') continue;
    const items = type.items;
    if (items === null || items === undefined) continue;
    if (isRef(items) || isRecursive(items)) continue;
    for (const [key, value] of Object.entries(items)) {
      if (ALREADY_ON_THE_ROW.has(key)) continue;
      said.push(...literals(key, value));
    }
  }
  return said;
}

/**
 * The text a facet's value puts on the page, for the facets that are text.
 *
 * An example is a *record* -- its value beside the metadata written with it --
 * so the text is one level down. Reading the record itself found nothing, which
 * is a check quietly measuring less rather than failing: the count fell from
 * four to three and every assertion still passed.
 */
function literals(key: string, value: unknown): string[] {
  if (typeof value === 'string' || typeof value === 'number') return [String(value)];
  if (key === 'example') return literals('', (value as Example).value);
  if (key === 'examples') return Object.values(value as Record<string, Example>).flatMap((one) => literals('', one.value));
  return [];
}

/**
 * The spellings an array must *not* be given: its own expression, suffixed.
 *
 * The array half of the union check above. An array written as an expression
 * stamps that expression onto its items -- `sources?: string[]` gives items
 * whose `type_expr` is `string[]` -- so a renderer that spells the items from
 * it and then appends the array's `[]` produces `string[][]`, which is a
 * different type. An array written `type: array` is immune, its items carrying
 * their own `string`, so every array in the sample read correctly until one was
 * written the other way.
 *
 * Derived from the tree rather than from a list here: the forbidden string is
 * the container's own `type_expr` plus `[]`, which is wrong for exactly the
 * shapes that borrowed it and says nothing about any other.
 */
function doubled(shape: Shape): string[] {
  const found: string[] = [];
  walk(shape, (one) => {
    const written = one.type_expr;
    if (one.type !== 'array' || written === undefined) return;
    const items = one.items;
    if (items == null) return;
    if (isRef(items) || isRecursive(items)) return;
    if (items.type_expr === written) found.push(`${written}[]`);
  });
  return found;
}

/** Text as React writes it into markup, so a `pattern` full of punctuation matches. */
function escaped(text: string): string {
  const as: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;' };
  return text.replace(/[&<>"']/g, (character) => as[character] ?? character);
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
 * So the expectation comes from the tree instead.
 */
function says(shape: Shape | undefined): boolean {
  if (!shape) return false;
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
const highlightedJson = highlightCode('{"answer": 42}', 'json');
if (!highlightedJson?.html.includes('hljs-attr') || !highlightedJson.html.includes('hljs-number')) {
  process.stderr.write('JSON syntax highlighting produced no coloured tokens\n');
  failed += 1;
}
if (highlightCode('Dune') !== null) {
  process.stderr.write('a plain string example was mistaken for source code\n');
  failed += 1;
}
const detectedCode = highlightCode('<book>\n  <title>Dune</title>\n</book>');
if (!detectedCode?.html.includes('hljs-tag')) {
  process.stderr.write('an unlabelled source example was not language-detected\n');
  failed += 1;
}
if (highlightCode('<script>alert(1)</script>')?.html.includes('<script>')) {
  process.stderr.write('syntax highlighting emitted an author-controlled tag\n');
  failed += 1;
}
const highlightedFence = renderMarkdown('```json\n{"answer": 42}\n```');
if (!highlightedFence.includes('hljs-attr') || !highlightedFence.includes('language-json')) {
  process.stderr.write('a labelled Markdown fence was not syntax highlighted\n');
  failed += 1;
}
process.stdout.write(`${HOSTILE.length} hostile descriptions checked\n`);

/*
 * A resource's method list names an operation by its description where it has
 * no `displayName`, and the description is Markdown. The sample names every
 * operation, so one is unnamed here: the row must read as rendered prose, and
 * as its first paragraph only.
 */
{
  const [path, endpoint] = Object.entries(document.endpoints).find(([, one]) => Object.keys(one.operations).length > 0)!;
  const [method, operation] = Object.entries(endpoint.operations)[0]!;
  const unnamed: Document = {
    ...document,
    endpoints: {
      ...document.endpoints,
      [path]: {
        ...endpoint,
        operations: { ...endpoint.operations, [method]: { ...operation, display_name: null, description: 'Lists *books*.\n\n- the rest' } },
      },
    },
  };
  const html = renderToStaticMarkup(
    <MemoryRouter initialEntries={[`/endpoints/${encodeURIComponent(path)}`]}>
      <Pages document={unnamed} index={new Index(Tree.of(unnamed))} />
    </MemoryRouter>,
  );
  if (!html.includes('<em>books</em>') || html.includes('*books*') || html.includes('the rest')) {
    process.stderr.write(`METHOD ${method.toUpperCase()} ${path}: an unnamed operation's description is not its rendered first paragraph\n`);
    failed += 1;
  }
}

/*
 * A number the page shows is the number the document carried.
 *
 * Two halves, because the failure has two causes and either one alone is a
 * document that lies about the API it describes.
 *
 * **A bound** is an exact decimal string the emitter wrote, so reading it needs
 * nothing -- but it has to *be* one. A bound that arrives as a JSON number has
 * already been through a double by the time anything here sees it, and
 * `maximum: 9223372036854775807` reads ...808 with no sign that it was ever
 * anything else.
 */
/** Long enough for every decimal a person writes, short enough to fit a line. */
const LONGEST_BOUND = 40;
const BOUNDS: ReadonlySet<string> = new Set(['minimum', 'maximum', 'multipleOf']);

let bounds = 0;
walk(document, (shape) => {
  for (const [name, value] of facetsOf(shape)) {
    if (!BOUNDS.has(name)) continue;
    bounds += 1;
    if (typeof value !== 'string' || !/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(value)) {
      process.stderr.write(`BOUND  ${shape.name ?? shape.id}: ${name} is ${JSON.stringify(value)}, not an exact decimal\n`);
      failed += 1;
    }
    // The explosion the ratio form produced: `1.7976931348623157e308` is an
    // integer, so its exact ratio is 309 digits, 292 of them zeros nobody wrote.
    if (typeof value === 'string' && value.length > LONGEST_BOUND) {
      process.stderr.write(`BOUND  ${shape.name ?? shape.id}: ${name} is ${value.length} characters long\n`);
      failed += 1;
    }
  }
});
if (bounds === 0) {
  process.stderr.write('no bound in the document; the check above is vacuous\n');
  failed += 1;
}

/*
 * **Example data** is the author's payload and stays a JSON number, so an
 * integer past 2^53 is rounded by `JSON.parse` itself -- before any of this
 * runs. `numbers.ts` keeps the literal instead; these are the cases that
 * separate keeping it from rounding it, and `JSON.parse` alone fails the first
 * four.
 */
const LITERALS: [string, string][] = [
  ['9223372036854775807', '9223372036854775807'],
  ['-9223372036854775808', '-9223372036854775808'],
  ['9007199254740993', '9007199254740993'],
  ['123456789012345678901234567890', '123456789012345678901234567890'],
  // Inside the safe range, and past it only in the exponent: a double holds
  // both exactly, so boxing them would be noise.
  ['9007199254740991', '9007199254740991'],
  ['1.7976931348623157e+308', '1.7976931348623157e+308'],
  ['0.1', '0.1'],
  ['-0', '0'],
  ['1e-7', '1e-7'],
];
for (const [literal, expected] of LITERALS) {
  const shown = stringify(parse(`{"v":${literal}}`) as { v: unknown }).slice('{"v":'.length, -1);
  if (shown !== expected) {
    process.stderr.write(`NUMBER ${literal} came back as ${shown}\n`);
    failed += 1;
  }
}

/*
 * And that `stringify` is `JSON.stringify` everywhere else.
 *
 * Against the document rather than against examples written here: it is the
 * only input with every shape of value in it, and an agreement checked on
 * three hand-written objects agrees with whatever those three happen to be.
 * Compared after a round trip through `JSON.parse`, which is the value with no
 * boxed literals in it.
 */
const plain = JSON.parse(readFileSync(source, 'utf-8')) as unknown;
for (const indent of [0, 2]) {
  const mine = stringify(plain, indent);
  const theirs = JSON.stringify(plain, null, indent);
  if (mine !== theirs) {
    const at = [...mine].findIndex((character, position) => character !== theirs[position]);
    process.stderr.write(`STRING indent ${indent} differs at ${at}: ${mine.slice(at, at + 60)}\n`);
    failed += 1;
  }
}
process.stdout.write(`${bounds} bounds exact, ${LITERALS.length} literals kept\n`);

/*
 * `facetsOf` is a deny-list, so a key the tree gains is a facet chip by default.
 *
 * That is the wrong default and cannot be changed here -- the view has no way
 * to know which of a shape's keys are a kind's facets. The generated contract
 * does: `tree.d.ts` puts them on the kind interfaces, written by the generator
 * from the kind classes. Reading those interfaces back is what makes the
 * deny-list a claim that can be false rather than a list that quietly rots.
 *
 * `projection` and `json_schema` arrived as chips holding a whole JSON Schema
 * before this existed.
 */
const contract = readFileSync('src/tree.d.ts', 'utf-8');
const kindInterfaces = [...contract.matchAll(/export interface \w+Shape extends ShapeBase \{(.*?)\n\}/gs)];
const declaredFacets = new Set(
  kindInterfaces.flatMap((block) => [...block[1]!.matchAll(/^\s{2}(\w+)\??:/gm)].map((field) => camel(field[1]!))),
);
declaredFacets.delete('type');
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
