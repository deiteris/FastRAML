/**
 * The shell: load a document, index it, and route.
 *
 * Routing on the *address* is the idea worth keeping. Every declaration has a
 * structural address, the same one `fastraml graph` prints, so `$ref` becomes an
 * ordinary link and `/n/<address>` opens whatever a query returned.
 *
 * Nothing renders here. A page lives in `pages/`, a piece of one in
 * `components/`, and this file holds the route table and the load.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { HashRouter, Route, Routes, useLocation, useNavigationType } from 'react-router';
import { SearchDialog } from './components/Search';
import { Sidebar } from './components/Sidebar';
import { Boundary } from './components/ui';
import { DEFAULT_SOURCE, loadDocument } from './load';
import { type Document, Index, Tree } from './model';
import {
  AnnotationTypeList,
  AnnotationTypePage,
  DocumentationList,
  DocumentationPage,
  EndpointPage,
  NotFound,
  OperationPage,
  Overview,
  ResolveAddress,
  SecurityList,
  SecuritySchemePage,
  TypeList,
  TypePage,
} from './pages';

export default function App() {
  return (
    // Hash routing: a built bundle is meant to be opened from a file path or
    // dropped on any static host, neither of which rewrites unknown paths.
    <HashRouter>
      <Shell />
    </HashRouter>
  );
}

function Shell() {
  const [document, setDocument] = useState<Document | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadDocument(DEFAULT_SOURCE)
      .then(setDocument)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  // One `Tree` per document: it indexes every addressed shape by the
  // generated walk, and `Index` adds the page routing on top.
  const index = useMemo(() => (document ? new Index(Tree.of(document)) : null), [document]);
  const main = useArrival(document, index);
  const { pathname } = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const menu = useRef<HTMLButtonElement>(null);
  // The drawer is for choosing a page; once one is chosen it is in the way.
  useEffect(() => setNavOpen(false), [pathname]);
  const [searching, setSearching] = useState(false);
  // Kept across openings: a reader who opened the wrong result comes back to
  // the list they chose from, not an empty field.
  const [query, setQuery] = useState('');
  const openSearch = useCallback(() => setSearching(true), []);
  useSearchKeys(openSearch);

  if (!document || !index) {
    return (
      <main className="loading">
        <h1>API reference</h1>
        {error ? (
          <>
            <p className="error">{error}</p>
          </>
        ) : (
          <p>Loading…</p>
        )}
      </main>
    );
  }

  const close = () => {
    setNavOpen(false);
    menu.current?.focus();
  };
  return (
    <div
      className={`app ${navOpen ? 'nav-is-open' : ''}`}
      onKeyDown={(event) => navOpen && event.key === 'Escape' && close()}
    >
      {/* Narrow windows only: the nav becomes a drawer behind this bar. A
          fixed 320px column beside the page left a phone about a third of
          the screen for the page. */}
      <header className="topbar">
        <button
          ref={menu}
          type="button"
          className="plain-button"
          aria-expanded={navOpen}
          aria-controls="sidebar"
          onClick={() => setNavOpen(!navOpen)}
        >
          Menu
        </button>
        <span className="topbar-title">{document.entry_point?.title ?? 'API reference'}</span>
        <button type="button" className="plain-button topbar-search" aria-haspopup="dialog" onClick={openSearch}>
          Search
        </button>
      </header>
      <Sidebar document={document} index={index} onSearch={openSearch} />
      {navOpen && <div className="scrim" onClick={close} />}
      <main ref={main} tabIndex={-1}>
        <Pages document={document} index={index} />
      </main>
      {searching && (
        <SearchDialog document={document} index={index} query={query} onQuery={setQuery} onClose={() => setSearching(false)} />
      )}
    </div>
  );
}

/**
 * `/` and Ctrl+K (Cmd+K on a Mac) open search from anywhere on the page.
 *
 * `/` only where it cannot be text: typed into a field it is a character. The
 * modifier chord has no such case, so it applies everywhere, and it is taken
 * from the browser, whose own Ctrl+K would move focus to its address bar.
 */
function useSearchKeys(open: () => void) {
  useEffect(() => {
    const keyed = (event: globalThis.KeyboardEvent) => {
      const chord = (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k';
      const target = event.target;
      const typing =
        target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
      const slash = event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !typing;
      if (!chord && !slash) return;
      event.preventDefault();
      open();
    };
    window.addEventListener('keydown', keyed);
    return () => window.removeEventListener('keydown', keyed);
  }, [open]);
}

/**
 * What arriving at a page does outside it: the window's title, where the window
 * is scrolled, and where focus is.
 *
 * The window scrolls, not `main`, and a route change remounts the page without
 * moving it -- so a link followed from the foot of a long operation opened the
 * next page as far down. Only on a push: going back is a traversal, and there
 * the browser restores the position the reader left. Focus moves to `main` for
 * the same reason a page load puts it at the top: a screen reader otherwise
 * stays on the link, in a page that is no longer there.
 */
function useArrival(document: Document | null, index: Index | null) {
  const { pathname, hash } = useLocation();
  const navigation = useNavigationType();
  const main = useRef<HTMLElement>(null);

  // One title per route, from the index that already names every page: a tab
  // strip of `API reference` and a history of identical entries say nothing.
  const titles = useMemo(() => {
    const named = new Map<string, string>(SECTIONS);
    for (const entry of index?.byAddress.values() ?? []) {
      const [method, ...path] = entry.name.split(' ');
      named.set(decoded(entry.href), entry.section === 'operation' ? `${method!.toUpperCase()} ${path.join(' ')}` : entry.name);
    }
    for (const [at, item] of (document?.entry_point?.documentation ?? []).entries()) named.set(`/documentation/${at}`, item.title);
    return named;
  }, [document, index]);

  useEffect(() => {
    if (!document) return;
    const site = document.entry_point?.title ?? 'API reference';
    const page = titles.get(decoded(pathname));
    window.document.title = page ? `${page} · ${site}` : site;
  }, [document, pathname, titles]);

  // A link to a section -- `#/endpoints/…/get#responses` -- lands on it. The
  // browser cannot do that itself: the whole of that is its fragment, and no
  // element has it as an id. On the first load as well, which is a POP too,
  // because a shared link to a section is opened that way.
  const first = useRef(true);
  useEffect(() => {
    if (!document) return;
    const initial = first.current;
    first.current = false;
    const target = hash ? window.document.getElementById(decoded(hash.slice(1))) : null;
    if (target && (initial || navigation !== 'POP')) {
      target.scrollIntoView();
      target.focus({ preventScroll: true });
      return;
    }
    if (navigation === 'POP') return;
    window.scrollTo(0, 0);
    main.current?.focus({ preventScroll: true });
  }, [document, pathname, hash, navigation]);

  return main;
}

const SECTIONS: [string, string][] = [
  ['/documentation', 'Documentation'],
  ['/types', 'Types'],
  ['/annotation-types', 'Annotation types'],
  ['/security', 'Security schemes'],
];

/** A route as one spelling: a link and the location it produced may escape differently. */
function decoded(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}

/**
 * Every route, over a document already loaded.
 *
 * Split from `Shell` so it can be mounted without fetching: `smoke.tsx` renders
 * each page of a real document through this, which is the only way to find a
 * component that compiles and throws.
 */
export function Pages({ document, index }: { document: Document; index: Index }) {
  const pages = { document, index };
  const { pathname } = useLocation();
  // Keyed on the path so a route change remounts. React reconciles by position,
  // so without this a `$ref` expanded under one type stays expanded at the same
  // position under the next -- showing a disclosure open on a shape nobody
  // opened, which reads as a property of the document. The boundary is keyed
  // the same way, so a page that failed does not stay failed on the next one.
  return (
    <Boundary key={pathname} what="page">
      <Routes>
        <Route path="/" element={<Overview {...pages} />} />
        <Route path="/documentation" element={<DocumentationList {...pages} />} />
        <Route path="/documentation/:at" element={<DocumentationPage {...pages} />} />
        <Route path="/endpoints/:path" element={<EndpointPage {...pages} />} />
        <Route path="/endpoints/:path/:method" element={<OperationPage {...pages} />} />
        <Route path="/types" element={<TypeList {...pages} />} />
        <Route path="/types/:file/:name" element={<TypePage {...pages} />} />
        <Route path="/annotation-types" element={<AnnotationTypeList {...pages} />} />
        <Route path="/annotation-types/:file/:name" element={<AnnotationTypePage {...pages} />} />
        <Route path="/security" element={<SecurityList {...pages} />} />
        <Route path="/security/:file/:name" element={<SecuritySchemePage {...pages} />} />
        <Route path="/n/:address" element={<ResolveAddress {...pages} />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Boundary>
  );
}
