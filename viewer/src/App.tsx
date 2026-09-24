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

import { useEffect, useMemo, useRef, useState } from 'react';
import { HashRouter, Route, Routes, useLocation, useNavigationType } from 'react-router';
import { Sidebar } from './components/Sidebar';
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

  return (
    <div className="app">
      <Sidebar document={document} index={index} />
      <main ref={main} tabIndex={-1}>
        <Pages document={document} index={index} />
      </main>
    </div>
  );
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
  const { pathname } = useLocation();
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

  useEffect(() => {
    if (navigation === 'POP') return;
    window.scrollTo(0, 0);
    main.current?.focus({ preventScroll: true });
  }, [pathname, navigation]);

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
  // opened, which reads as a property of the document.
  return (
    <Routes key={pathname}>
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
  );
}
