/** A viewer for a tree document, inside a router supplied by the host. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigationType } from 'react-router';
import { SearchDialog } from './components/Search';
import { Sidebar } from './components/Sidebar';
import { MenuIcon, storedTheme } from './components/ui';
import { parseDocument } from './load';
import { type Document, Index, Tree } from './model';
import { Pages } from './pages/Routes';

type ViewerProps = ({ document: Document; contents?: never } | { contents: string; document?: never }) & {
  managePage?: boolean;
  themeToggle?: boolean;
};

/** Supply a decoded tree or its JSON text; the host supplies the router. */
export function Viewer({ document, contents, managePage = false, themeToggle = true }: ViewerProps) {
  const parsed = useMemo(() => {
    if (contents === undefined) return null;
    try {
      return { document: parseDocument(contents), error: null };
    } catch (cause) {
      return { document: null, error: cause instanceof Error ? cause.message : String(cause) };
    }
  }, [contents]);
  const selected = document ?? parsed?.document;
  if (!selected) return <ViewerMessage error={parsed?.error ?? 'No API definition supplied.'} managePage={managePage} themeToggle={themeToggle} />;
  return <ViewerDocument document={selected} managePage={managePage} themeToggle={themeToggle} />;
}

function ViewerDocument({ document, managePage, themeToggle }: { document: Document; managePage: boolean; themeToggle: boolean }) {
  const [initialTheme] = useState(() => (themeToggle ? storedTheme() : null));
  const index = useMemo(() => new Index(Tree.of(document)), [document]);
  const revision = useRef({ document, number: 0 });
  if (revision.current.document !== document) {
    revision.current = { document, number: revision.current.number + 1 };
  }
  const main = useArrival(document, index, managePage);
  const { pathname } = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const menu = useRef<HTMLButtonElement>(null);
  useEffect(() => setNavOpen(false), [pathname, document]);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState('');
  useEffect(() => {
    setSearching(false);
    setQuery('');
  }, [document]);
  const openSearch = useCallback(() => setSearching(true), []);
  useSearchKeys(openSearch, managePage);

  const close = () => {
    setNavOpen(false);
    menu.current?.focus();
  };
  return (
    <div className="fastraml-viewer-frame">
      <div
        className={`fastraml-viewer ${navOpen ? 'nav-is-open' : ''} ${managePage ? 'viewer-standalone' : ''}`}
        data-theme={initialTheme ?? undefined}
        onKeyDown={(event) => navOpen && event.key === 'Escape' && close()}
      >
        <header className="topbar">
          <button
            ref={menu}
            type="button"
            className="plain-button icon-button"
            aria-label="Menu"
            aria-expanded={navOpen}
            aria-controls="sidebar"
            onClick={() => setNavOpen(!navOpen)}
          >
            <MenuIcon />
          </button>
          <span className="topbar-title">{document.entry_point?.title ?? 'API reference'}</span>
          <button type="button" className="plain-button topbar-search" aria-haspopup="dialog" onClick={openSearch}>
            Search
          </button>
        </header>
        <Sidebar document={document} index={index} onSearch={openSearch} shortcuts={managePage} projectLink={managePage} themeToggle={themeToggle} />
        {navOpen && <div className="scrim" onClick={close} />}
        <main ref={main} tabIndex={-1}>
          <Pages key={revision.current.number} document={document} index={index} />
        </main>
        {searching && (
          <SearchDialog
            key={revision.current.number}
            document={document}
            index={index}
            query={query}
            onQuery={setQuery}
            onClose={() => setSearching(false)}
          />
        )}
      </div>
    </div>
  );
}

/** Fetching is optional: an embedding app can load its own document and pass it to Viewer. */
export function LoadedViewer({
  load,
  managePage = false,
  themeToggle = true,
}: {
  load: () => Promise<Document>;
  managePage?: boolean;
  themeToggle?: boolean;
}) {
  const [document, setDocument] = useState<Document | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setDocument(null);
    setError(null);
    Promise.resolve()
      .then(load)
      .then(
        (result) => {
          if (active) setDocument(result);
        },
        (cause: unknown) => {
          if (active) setError(cause instanceof Error ? cause.message : String(cause));
        },
      );
    return () => {
      active = false;
    };
  }, [load]);
  if (document) return <Viewer document={document} managePage={managePage} themeToggle={themeToggle} />;
  return <ViewerMessage error={error} managePage={managePage} themeToggle={themeToggle} />;
}

function ViewerMessage({ error, managePage, themeToggle }: { error: string | null; managePage: boolean; themeToggle: boolean }) {
  const [initialTheme] = useState(() => (themeToggle ? storedTheme() : null));
  return (
    <div className="fastraml-viewer-frame">
      <div className={`fastraml-viewer viewer-loading ${managePage ? 'viewer-standalone' : ''}`} data-theme={initialTheme ?? undefined}>
        <main className="loading">
          <h1>API reference</h1>
          {error ? <p className="error">{error}</p> : <p>Loading…</p>}
        </main>
      </div>
    </div>
  );
}

/** The standalone page owns global search shortcuts; embedded hosts choose their own. */
function useSearchKeys(open: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
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
  }, [open, enabled]);
}

/** On a standalone page, route arrival sets the title, scroll, and focus. */
function useArrival(document: Document, index: Index, managePage: boolean) {
  const { pathname, hash } = useLocation();
  const navigation = useNavigationType();
  const main = useRef<HTMLElement>(null);

  const titles = useMemo(() => {
    if (!managePage) return new Map<string, string>();
    const named = new Map<string, string>(SECTIONS);
    for (const entry of index.byAddress.values()) {
      const [method, ...path] = entry.name.split(' ');
      named.set(decoded(entry.href), entry.section === 'operation' ? `${method!.toUpperCase()} ${path.join(' ')}` : entry.name);
    }
    for (const [at, item] of (document.entry_point?.documentation ?? []).entries()) named.set(`/documentation/${at}`, item.title);
    return named;
  }, [document, index, managePage]);

  useEffect(() => {
    if (!managePage) return;
    const site = document.entry_point?.title ?? 'API reference';
    const page = titles.get(decoded(pathname));
    window.document.title = page ? `${page} · ${site}` : site;
  }, [document, pathname, titles, managePage]);

  const first = useRef(true);
  useEffect(() => {
    const initial = first.current;
    first.current = false;
    const target = hash ? main.current?.querySelector<HTMLElement>(`[id="${CSS.escape(decoded(hash.slice(1)))}"]`) : null;
    if (target && (initial || navigation !== 'POP')) {
      target.scrollIntoView();
      target.focus({ preventScroll: true });
      return;
    }
    if (!managePage || navigation === 'POP') return;
    window.scrollTo(0, 0);
    main.current?.focus({ preventScroll: true });
  }, [document, pathname, hash, navigation, managePage]);

  return main;
}

const SECTIONS: [string, string][] = [
  ['/documentation', 'Documentation'],
  ['/types', 'Types'],
  ['/annotation-types', 'Annotation types'],
  ['/security', 'Security schemes'],
];

function decoded(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}
