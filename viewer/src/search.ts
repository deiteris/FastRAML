/**
 * Search: what the search dialog matches a query against.
 *
 * The matching is Fuse.js's. What is here is what Fuse cannot know: which
 * things a reader looks for, what each is called, which page shows it, and
 * what its prose says once the Markdown is gone.
 *
 * It decides no RAML rule. It reads names, display names and descriptions the
 * tree already carries.
 *
 * **Every word of the query must match.** Token search with `tokenMatch:
 * 'all'`: `get books` narrows to the operations that are both, where one
 * pattern over the whole query would look for the phrase.
 *
 * **A name outranks a display name, which outranks prose.** Fuse's key
 * weights; ties keep declaration order, which is the order the nav shows.
 *
 * **What is shown is what is searched**, so a match can always be marked where
 * the reader sees it. Prose is searched as the text a reader sees, not as its
 * Markdown source: `[the guide](https://example.com/books)` matches `guide` and
 * not `books`, because the URL is never shown and a result that matched on it
 * could not point at why.
 */

import Fuse, { type FuseResultMatch, type IFuseOptions, type RangeTuple } from 'fuse.js';
import MarkdownIt, { type Token } from 'markdown-it';
import { type Document, type Index, declarations, hrefOf, isRef, methodsOf, operationHref } from './model';

export type Category = 'documentation' | 'endpoint' | 'operation' | 'type' | 'annotationType' | 'securityScheme';

/** What each group is called, in the nav's words. */
const LABELS: Record<Category, string> = {
  documentation: 'Documentation',
  endpoint: 'Endpoints',
  operation: 'Operations',
  type: 'Types',
  annotationType: 'Annotation types',
  securityScheme: 'Security schemes',
};

export interface SearchEntry {
  category: Category;
  /** What the row is called: a path, a declared name, a documentation title. */
  title: string;
  /**
   * An operation's verb, drawn as a badge beside its path. Searched with the
   * title: most operations have no display name, and `GET /books` is how a
   * reader who knows the API refers to one.
   */
  method?: string;
  /** A second name: a display name, or a security scheme's type. */
  detail?: string;
  /** The file a declaration was written in. Two libraries may both say `Book`. */
  file?: string;
  /** The description or content, as plain text. */
  prose?: string;
  href: string;
}

/** A matched text, cut into runs, each marked if it is part of the match. */
export type Runs = [string, boolean][];

export interface Result {
  entry: SearchEntry;
  /** The title, with what matched in it marked. */
  title: Runs;
  /** The detail, likewise: `measure` finds `Tolerance` by its display name. */
  detail?: Runs;
  /** The prose around a match, or its start when only a name matched. */
  excerpt?: Runs;
}

export interface Group {
  category: Category;
  label: string;
  results: Result[];
}

/** Shorter runs are the stray letters a fuzzy match marks on its way. */
const SHORTEST = 2;

const OPTIONS: IFuseOptions<SearchEntry> = {
  keys: [
    { name: 'title', weight: 3 },
    { name: 'method', weight: 3 },
    { name: 'detail', weight: 2 },
    { name: 'prose', weight: 1 },
  ],
  useTokenSearch: true,
  tokenMatch: 'all',
  includeMatches: true,
  // Where in a description a word falls says nothing about relevance.
  ignoreLocation: true,
  // Close enough for a dropped letter; at Fuse's default, `isbn` found `is`.
  threshold: 0.2,
  minMatchCharLength: SHORTEST,
};

export class SearchIndex {
  readonly entries: SearchEntry[] = [];
  private readonly fuse: Fuse<SearchEntry>;

  constructor(document: Document, index: Index) {
    const entries = this.entries;

    for (const [at, item] of (document.entry_point?.documentation ?? []).entries()) {
      entries.push({ category: 'documentation', title: item.title, prose: plainText(item.content), href: `/documentation/${at}` });
    }

    for (const [path, endpoint] of Object.entries(document.endpoints)) {
      entries.push({
        category: 'endpoint',
        title: path,
        detail: endpoint.display_name,
        prose: plainText(endpoint.description),
        href: hrefOf('endpoint', path),
      });
      for (const [method, operation] of methodsOf(endpoint)) {
        entries.push({
          category: 'operation',
          title: path,
          method,
          detail: operation.display_name,
          prose: plainText(operation.description),
          href: operationHref(path, method),
        });
      }
    }

    for (const [category, byFile, fallback] of [
      ['type', document.types, '/types'],
      ['annotationType', document.annotation_types, '/annotation-types'],
    ] as const) {
      for (const { file, name, value } of declarations(byFile)) {
        // A declaration that is a `$ref` has no prose of its own; its page is
        // its target's, which is where the nav sends it too.
        const shape = isRef(value) ? undefined : value;
        entries.push({
          category,
          title: category === 'annotationType' ? `(${name})` : name,
          detail: shape?.display_name,
          file,
          prose: plainText(shape?.description),
          href: index.declaration(value)?.href ?? fallback,
        });
      }
    }

    for (const { file, name, value } of declarations(document.security_schemes)) {
      entries.push({
        category: 'securityScheme',
        title: name,
        detail: value.display_name ? `${value.display_name} · ${value.type}` : value.type,
        file,
        prose: plainText(value.description),
        href: hrefOf('securityScheme', name, file),
      });
    }

    this.fuse = new Fuse(entries, OPTIONS);
  }

  /** Every entry the query matches, grouped, best first within each group. */
  find(query: string): Group[] {
    if (query.trim() === '') return [];
    // Groups in the order of their best result, which is the order Fuse first
    // produces them in. In the nav's fixed order, four documentation pages
    // that mention a book in passing pushed the type named `Book` off screen.
    const found = new Map<Category, Result[]>();
    for (const { item, matches = [] } of this.fuse.search(query.trim())) {
      const results = found.get(item.category) ?? [];
      results.push({
        entry: item,
        title: runsOf(item.title, rangesOf(matches, 'title')),
        detail: item.detail === undefined ? undefined : runsOf(item.detail, rangesOf(matches, 'detail')),
        excerpt: excerptOf(item.prose, rangesOf(matches, 'prose')),
      });
      found.set(item.category, results);
    }
    return [...found].map(([category, results]) => ({ category, label: LABELS[category], results }));
  }
}

const built = new WeakMap<Document, SearchIndex>();

/** The index for a document, built the first time a search needs it. */
export function searchIndexOf(document: Document, index: Index): SearchIndex {
  let found = built.get(document);
  if (!found) {
    found = new SearchIndex(document, index);
    built.set(document, found);
  }
  return found;
}

/* -- showing why a result matched ------------------------------------------------ */

const BEFORE = 40;
const AFTER = 110;

/**
 * A window of the prose.
 *
 * Around the first match when the prose is what matched; its start otherwise,
 * so a result named `Book` still says what a Book is.
 */
function excerptOf(prose: string | undefined, ranges: RangeTuple[]): Runs | undefined {
  if (!prose) return undefined;
  const first = ranges.length > 0 ? Math.min(...ranges.map(([start]) => start)) : 0;
  // Start on a word, not inside one, and end on one.
  let start = Math.max(0, first - BEFORE);
  if (start > 0) start = Math.min(prose.indexOf(' ', start) + 1 || first, first);
  let end = Math.min(prose.length, first + AFTER);
  if (end < prose.length) end = Math.max(prose.lastIndexOf(' ', end), first + 1);
  const window = ranges.filter(([s, e]) => s >= start && e < end).map(([s, e]): RangeTuple => [s - start, e - start]);
  const runs = runsOf(prose.slice(start, end), window);
  if (start > 0) runs.unshift(['… ', false]);
  if (end < prose.length) runs.push([' …', false]);
  return runs;
}

/** Fuse's inclusive ranges for one key, less the single letters a fuzzy match strews about. */
function rangesOf(matches: readonly FuseResultMatch[], key: string): RangeTuple[] {
  return matches
    .filter((match) => match.key === key)
    .flatMap((match) => match.indices)
    .filter(([start, end]) => end - start + 1 >= SHORTEST);
}

/** Text cut at inclusive `[start, end]` ranges, which may overlap. */
function runsOf(text: string, ranges: readonly RangeTuple[]): Runs {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  const runs: Runs = [];
  let cursor = 0;
  for (const [start, end] of sorted) {
    const from = Math.max(start, cursor);
    const to = Math.min(end + 1, text.length);
    if (to <= from) continue;
    if (from > cursor) runs.push([text.slice(cursor, from), false]);
    runs.push([text.slice(from, to), true]);
    cursor = to;
  }
  if (cursor < text.length) runs.push([text.slice(cursor), false]);
  return runs;
}

/* -- Markdown to text ------------------------------------------------------------ */

// Parsing only: nothing here is rendered, so neither `linkify` nor highlighting
// has anything to do. `html: false` keeps a tag in the source as text, the way
// the page shows it.
const md = new MarkdownIt({ html: false, linkify: false });

/**
 * What a reader sees of a Markdown source, as one line of text.
 *
 * Text, inline code, image alt text and code blocks; not link targets, not
 * emphasis markers, not heading hashes.
 */
export function plainText(source: string | undefined): string | undefined {
  if (!source || source.trim() === '') return undefined;
  const parts: string[] = [];
  const collect = (tokens: Token[]) => {
    for (const token of tokens) {
      if (token.type === 'text' || token.type === 'code_inline' || token.type === 'fence' || token.type === 'code_block') {
        parts.push(token.content);
      } else if (token.type === 'softbreak' || token.type === 'hardbreak') {
        parts.push(' ');
      }
      if (token.children) collect(token.children);
      // A block ends a word even where the source ran two blocks together.
      if (token.block && token.nesting === -1) parts.push(' ');
    }
  };
  collect(md.parse(source, {}));
  return parts.join('').replace(/\s+/g, ' ').trim() || undefined;
}
