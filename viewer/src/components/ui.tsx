/** Small pieces every page uses. Nothing here knows about RAML. */

import { type ReactNode, useState } from 'react';

export function Chip({ tone = 'plain', title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export type Tone = 'plain' | 'type' | 'required' | 'optional' | 'method' | 'status' | 'warn' | 'recursive';

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
      <button type="button" className="disclosure-summary" onClick={() => setOpen(!isOpen)}>
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
