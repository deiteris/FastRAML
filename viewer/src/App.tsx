/**
 * The shell: load a document, index it, and route.
 *
 * Routing on the *address* is the idea worth keeping. Every declaration has a
 * structural address, the same one `pyraml graph` prints, so `$ref` becomes an
 * ordinary link and `/n/<address>` opens whatever a query returned.
 */

import { useEffect, useMemo, useState } from 'react';
import { HashRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router';
import { Empty } from './components/ui';
import { DEFAULT_SOURCE, loadDocument, readFile } from './load';
import { type Document, Index, pathTree, type PathNode, declarations } from './model';
import {
  AnnotationTypeList,
  AnnotationTypePage,
  EndpointPage,
  NotFound,
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
    const source = new URLSearchParams(window.location.search).get('src') ?? DEFAULT_SOURCE;
    loadDocument(source)
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
              Generate one with <code>pyraml tree FILE &gt; api.json</code>, then open it below.
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
      <Route path="/endpoints/:path" element={<EndpointPage {...pages} />} />
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

function FilePicker({ onOpen }: { onOpen: (file: File) => void }) {
  return (
    <label className="picker">
      <input
        type="file"
        accept="application/json,.json"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onOpen(file);
        }}
      />
      <span>Open a tree.json…</span>
    </label>
  );
}

function Sidebar({
  document,
  index,
  onOpen,
}: {
  document: Document;
  index: Index;
  onOpen: (file: File) => void;
}) {
  const [filter, setFilter] = useState('');
  const roots = useMemo(() => pathTree(document.endpoints), [document]);
  const matches = (text: string) => !filter || text.toLowerCase().includes(filter.toLowerCase());

  const types = declarations(document.types).filter(({ name }) => matches(name));
  const annotationTypes = declarations(document.annotation_types).filter(({ name }) => matches(name));
  const schemes = declarations(document.security_schemes).filter(({ name }) => matches(name));

  return (
    <nav className="sidebar">
      <Link to="/" className="brand">
        {document.entry_point?.title ?? 'API reference'}
      </Link>
      <input
        className="filter"
        type="search"
        placeholder="Filter…"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />

      <NavGroup title="Endpoints">
        {roots.map((node) => (
          <PathBranch key={node.path} node={node} matches={matches} />
        ))}
      </NavGroup>

      <NavGroup title="Types" href="/types">
        {types.map(({ file, name, value }) => (
          <NavItem key={`${file}/${name}`} to={index.get(value.id)?.href ?? '/types'} label={name} />
        ))}
      </NavGroup>

      {annotationTypes.length > 0 && (
        <NavGroup title="Annotation types" href="/annotation-types">
          {annotationTypes.map(({ file, name, value }) => (
            <NavItem key={`${file}/${name}`} to={index.get(value.id)?.href ?? '/annotation-types'} label={`(${name})`} />
          ))}
        </NavGroup>
      )}

      {schemes.length > 0 && (
        <NavGroup title="Security" href="/security">
          {schemes.map(({ file, name, value }) => (
            <NavItem key={`${file}/${name}`} to={index.get(value.id)?.href ?? '/security'} label={name} />
          ))}
        </NavGroup>
      )}

      <div className="sidebar-foot">
        <FilePicker onOpen={onOpen} />
      </div>
    </nav>
  );
}

/**
 * One path segment and everything under it.
 *
 * A branch with no `endpoint` is a segment nothing declared methods on -- the
 * `/books` in a document that only writes `/books/{isbn}`. It is shown, because
 * leaving it out would break the nesting, and it is not a link, because there
 * is nothing to show.
 */
function PathBranch({ node, matches }: { node: PathNode; matches: (text: string) => boolean }) {
  const relevant = matches(node.path) || node.children.some((child) => within(child, matches));
  if (!relevant) return null;
  return (
    <li>
      {node.endpoint ? (
        <NavItem to={`/endpoints/${encodeURIComponent(node.path)}`} label={node.segment} />
      ) : (
        <span className="nav-item is-empty">{node.segment}</span>
      )}
      {node.children.length > 0 && (
        <ul className="nav-children">
          {node.children.map((child) => (
            <PathBranch key={child.path} node={child} matches={matches} />
          ))}
        </ul>
      )}
    </li>
  );
}

function within(node: PathNode, matches: (text: string) => boolean): boolean {
  return matches(node.path) || node.children.some((child) => within(child, matches));
}

function NavGroup({ title, href, children }: { title: string; href?: string; children: React.ReactNode }) {
  const empty = Array.isArray(children) && children.flat().filter(Boolean).length === 0;
  return (
    <div className="nav-group">
      <h3>{href ? <NavLink to={href}>{title}</NavLink> : title}</h3>
      {empty ? <p className="nav-empty">none</p> : <ul>{children}</ul>}
    </div>
  );
}

function NavItem({ to, label }: { to: string; label: string }) {
  const { pathname } = useLocation();
  return (
    <li>
      <NavLink to={to} className={`nav-item ${decodeURIComponent(pathname) === decodeURIComponent(to) ? 'is-here' : ''}`}>
        {label}
      </NavLink>
    </li>
  );
}

export { Empty };
