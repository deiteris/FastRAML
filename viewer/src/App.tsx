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

import { useEffect, useMemo, useState } from 'react';
import { HashRouter, Route, Routes, useLocation } from 'react-router';
import { FilePicker } from './components/FilePicker';
import { Sidebar } from './components/Sidebar';
import { DEFAULT_SOURCE, loadDocument, readFile } from './load';
import { type Document, Index } from './model';
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

  const index = useMemo(() => (document ? new Index(document) : null), [document]);

  const open = (file: File) => {
    readFile(file)
      .then((loaded) => {
        setDocument(loaded);
        setError(null);
      })
      .catch((cause: Error) => setError(cause.message));
  };

  if (!document || !index) {
    return (
      <main className="loading">
        <h1>API reference</h1>
        {error ? (
          <>
            <p className="error">{error}</p>
            <p>
              Generate one with <code>fastraml tree FILE &gt; api.json</code>, then open it below.
            </p>
          </>
        ) : (
          <p>Loading…</p>
        )}
        <FilePicker onOpen={open} />
      </main>
    );
  }

  return (
    <div className="app">
      <Sidebar document={document} index={index} onOpen={open} />
      <main>
        <Pages document={document} index={index} />
      </main>
    </div>
  );
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
