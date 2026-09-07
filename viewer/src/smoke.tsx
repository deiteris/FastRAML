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
import { Index, declarations, type Document } from './model';

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
process.exit(failed === 0 ? 0 : 1);
