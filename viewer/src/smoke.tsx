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
import { Index, declarations, isRecursive, isRef, labelOf, type Document, type Shape } from './model';

const source = process.argv[2] ?? 'public/api.json';
const document = JSON.parse(readFileSync(source, 'utf-8')) as Document;
const index = new Index(document);

const routes = [
  '/',
  '/types',
  '/annotation-types',
  '/security',
  '/nonsense',
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
  // Stated as the rule and not as "does not equal the parent's expression",
  // which is what this asked first and why it passed on a query parameter
  // typed `Search`: the parent read `Search`, the members read `string |
  // number`, the two differed, and both members were still wrong.
  for (const member of shape.any_of ?? []) {
    members += 1;
    const label = labelOf(member, index);
    const own = isRef(member)
      ? index.label(member.$ref)
      : isRecursive(member)
        ? (member.name ?? 'recursive')
        : member.type;
    if (label !== own) {
      process.stderr.write(`UNION  ${shape.name ?? shape.id}: member reads "${label}", its own type is "${own}"\n`);
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
