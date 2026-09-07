/** Small pieces every page uses. Nothing here knows about RAML. */

import { type ReactNode, useEffect, useState } from 'react';

export function Chip({ tone = 'plain', title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

/**
 * A disclosure chevron.
 *
 * Inline SVG on a fixed 16-unit viewBox, so open and closed occupy the same box
 * and every row in the nav lines up. The characters this replaced -- a chevron
 * and a single angle quote -- have different widths and baselines, which is why
 * the rows did not.
 *
 * Not an icon package. `lucide-react` resolved its own copy of React and every
 * icon threw `Invalid hook call` on the first render; two chevrons are not
 * worth a dependency that can do that.
 */
export function Chevron({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path
        d={open ? 'M4 6.5 8 10.5 12 6.5' : 'M6.5 4 10.5 8 6.5 12'}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * A padlock, closed or open.
 *
 * Open means the operation may also be called unauthenticated -- a
 * `securedBy: [null]` entry among its alternatives. Swagger UI's convention,
 * and worth borrowing: a reader scanning for what needs a token should not have
 * to read every row to find out.
 */
export function Lock({ open, title }: { open?: boolean; title?: string }) {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" className="lock" role="img" aria-label={title}>
      {title && <title>{title}</title>}
      <rect x="3.25" y="7" width="9.5" height="6.5" rx="1.5" fill="currentColor" />
      <path
        d={open ? 'M5.75 7V4.9a2.4 2.4 0 0 1 4.8 0' : 'M5.75 7V4.9a2.4 2.4 0 0 1 4.8 0V7'}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** An HTTP method, as a coloured badge. */
export function Verb({ method, large }: { method: string; large?: boolean }) {
  const name = method.toLowerCase();
  return <span className={`verb verb-${name} ${large ? 'verb-lg' : ''}`}>{method.toUpperCase()}</span>;
}

export type Tone = 'plain' | 'type' | 'required' | 'optional' | 'method' | 'status' | 'warn' | 'recursive' | 'enum';

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <h2>
        {title}
        {aside && <span className="aside">{aside}</span>}
      </h2>
      {children}
    </section>
  );
}

/**
 * A block that starts closed.
 *
 * The default matters: a `$ref` that expanded on render would follow every link
 * eagerly, which is what an inlining consumer does and what a cyclic document
 * punishes. Opening is a click, so a cycle costs one click per lap.
 */
export function Disclosure({
  summary,
  children,
  open = false,
}: {
  summary: ReactNode;
  children: ReactNode;
  open?: boolean;
}) {
  const [isOpen, setOpen] = useState(open);
  return (
    <div className={`disclosure ${isOpen ? 'is-open' : ''}`}>
      <button type="button" className="disclosure-summary" aria-expanded={isOpen} onClick={() => setOpen(!isOpen)}>
        {/* A whole clickable row rather than a glyph: a 10px caret beside a
            chip reads as decoration on the chip, so nothing said the row was a
            control. The caret stays as the state indicator. */}
        <span className="disclosure-caret">{isOpen ? '▾' : '▸'}</span>
        {summary}
      </button>
      {isOpen && <div className="disclosure-body">{children}</div>}
    </div>
  );
}

/**
 * Prose, rendered as text.
 *
 * RAML descriptions are Markdown, and this shows them verbatim rather than
 * parsing them. A Markdown renderer is a dependency and an injection surface,
 * and the point of this app is the model, not the prose.
 */
export function Prose({ children }: { children?: string }) {
  if (!children) return null;
  return <p className="prose">{children}</p>;
}

export function Code({ children }: { children: unknown }) {
  const text = typeof children === 'string' ? children : JSON.stringify(children, null, 2);
  return <pre className="code">{text}</pre>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  const present = rows.filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (present.length === 0) return null;
  return (
    <dl className="keyvalues">
      {present.map(([key, value]) => (
        <div key={key} className="keyvalue">
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A tab strip over whole panels.
 *
 * Used wherever a set of alternatives is each a *whole* thing to read: the
 * members of a union, the responses of an operation. Stacked, they run together
 * -- two object members produce two attribute lists with nothing between them
 * saying where the first ended, and eight response codes produce eight
 * collapsed rows a reader has to open one at a time.
 */
export function Tabs({ label, items }: { label?: string; items: Tab[] }) {
  const [chosen, setChosen] = useState(0);
  if (items.length === 0) return null;
  const at = Math.min(chosen, items.length - 1);
  return (
    <div className="tabs">
      <div className="tabs-strip">
        {label && <span className="label">{label}</span>}
        {items.map((item, position) => (
          <button
            key={item.key}
            type="button"
            className={`tab tab-${item.tone ?? 'plain'} ${position === at ? 'is-chosen' : ''}`}
            aria-selected={position === at}
            onClick={() => setChosen(position)}
          >
            {item.label}
            {item.note && <span className="tab-note">{item.note}</span>}
          </button>
        ))}
      </div>
      <div className="tabs-panel">{items[at]?.body}</div>
    </div>
  );
}

export interface Tab {
  key: string;
  label: ReactNode;
  /** A second line inside the tab -- a response's description, say. */
  note?: ReactNode;
  /** A CSS suffix: a response's first digit, so the strip can colour it. */
  tone?: string;
  body: ReactNode;
}

/**
 * Light, dark, or whatever the system says.
 *
 * Three states and not two: a reader who has not chosen should follow the
 * system, and a toggle with two states silently makes that choice for them the
 * first time they use it. `data-theme` is absent in the third state, which is
 * how the stylesheet's media query stays in charge.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('theme') as Theme | null) ?? 'system');

  useEffect(() => {
    const root = window.document.documentElement;
    if (theme === 'system') {
      root.removeAttribute('data-theme');
      localStorage.removeItem('theme');
    } else {
      root.setAttribute('data-theme', theme);
      localStorage.setItem('theme', theme);
    }
  }, [theme]);

  const next: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' };
  const shown: Record<Theme, string> = { system: 'Theme: system', light: 'Theme: light', dark: 'Theme: dark' };
  return (
    <button type="button" className="theme" onClick={() => setTheme(next[theme])}>
      {shown[theme]}
    </button>
  );
}

type Theme = 'system' | 'light' | 'dark';
