/** Small pieces every page uses. Nothing here knows about RAML. */

import { Component, type KeyboardEvent, type ReactNode, useCallback, useEffect, useId, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router';

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

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/**
 * A heading a reader can link to, where it is given an `anchor`.
 *
 * The link is the route plus a fragment, `#/endpoints/…/get#query-parameters`:
 * the router keeps the part after the second `#` as its own hash, and the shell
 * scrolls to it on arrival. A `#` beside the title rather than the title as a
 * link, because a heading that navigates reads as the way to somewhere else.
 *
 * Without an anchor it is a plain heading. The same table is titled `Headers`
 * on an operation and inside every response tab, and two ids on one page would
 * make a link land on whichever came first.
 */
export function Heading({ level, anchor, children }: { level: 2 | 4; anchor?: string; children: ReactNode }) {
  const Tag = `h${level}` as const;
  const { pathname } = useLocation();
  return (
    <Tag id={anchor} tabIndex={anchor ? -1 : undefined} className={anchor ? 'anchored' : undefined}>
      {children}
      {anchor && (
        <Link to={{ pathname, hash: anchor }} className="anchor" aria-label="Link to this section">
          #
        </Link>
      )}
    </Tag>
  );
}

/**
 * Copy a text, and say so for a moment.
 *
 * The label changes rather than a toast appearing: the confirmation is where
 * the reader is looking, and a screen reader hears it through `aria-live`.
 */
export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState<'copied' | 'failed' | null>(null);
  useEffect(() => {
    if (done === null) return;
    const clear = setTimeout(() => setDone(null), 1500);
    return () => clearTimeout(clear);
  }, [done]);
  const copy = () => {
    // `clipboard` is absent outside a secure context -- a bundle opened over
    // plain http from another machine -- and the write can be refused.
    const write = navigator.clipboard?.writeText(text);
    if (!write) return setDone('failed');
    write.then(
      () => setDone('copied'),
      () => setDone('failed'),
    );
  };
  return (
    <button type="button" className="plain-button copy" onClick={copy} aria-live="polite">
      {done === 'copied' ? 'Copied' : done === 'failed' ? 'Copy failed' : label}
    </button>
  );
}

/**
 * A part of a page that fails alone.
 *
 * A document the reader is writing under `fastraml serve` is not the sample
 * `smoke` renders, and one shape this app misreads threw away the whole page,
 * nav included. Now it costs the section it is in, and says which.
 *
 * `smoke` renders to static markup, where a boundary catches nothing, so an
 * exception there still fails the run.
 */
export class Boundary extends Component<{ what: string; children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <p className="error" role="alert">
        The {this.props.what} could not be shown: {this.state.error.message}
      </p>
    );
  }
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
 *
 * **One line, scrolled -- not wrapped.** A strip is the index to the panel under
 * it, and an index whose height depends on the window is not one: a ten-member
 * union wrapped to three rows, and the rule under the chosen tab then pointed at
 * a panel two rows away. Scrolling keeps the strip one row at every width, and
 * the strip is the only thing that moves.
 *
 * The chevrons only appear where there is something to reach. They are laid out
 * beside the strip rather than over it, because an overlay covers the first and
 * last tab -- the two a reader scrolling is trying to read.
 *
 * The WAI-ARIA tabs pattern, so assistive technology announces it as one: one
 * tab stop for the strip, the arrow keys, Home and End to move along it.
 */
export function Tabs({ label, items }: { label?: string; items: Tab[] }) {
  const [chosen, setChosen] = useState(0);
  const id = useId();
  const strip = useRef<HTMLDivElement>(null);
  const [reach, setReach] = useState<Reach>(FITS);
  const at = Math.min(chosen, Math.max(items.length - 1, 0));

  const measure = useCallback(() => {
    const node = strip.current;
    if (!node) return;
    const slack = node.scrollWidth - node.clientWidth;
    // The threshold is a pixel, not zero: a fractional layout leaves sub-pixel
    // slack on a strip that fits, and chevrons that scroll nothing are worse
    // than none.
    setReach(slack <= 1 ? FITS : { back: node.scrollLeft > 1, on: node.scrollLeft < slack - 1 });
  }, []);

  // The strip's width follows the window and its content follows the document,
  // so neither a resize listener nor a render alone sees every change.
  useEffect(() => {
    const node = strip.current;
    if (!node) return;
    const watch = new ResizeObserver(measure);
    watch.observe(node);
    for (const tab of node.children) watch.observe(tab);
    return () => watch.disconnect();
  }, [measure, items]);

  // Choosing a tab the strip had scrolled past leaves the underline off-screen,
  // pointing at nothing. Written by hand rather than with `scrollIntoView`,
  // which also scrolls every ancestor -- including the page.
  useEffect(() => {
    const node = strip.current;
    const tab = node?.children[at];
    if (!node || !(tab instanceof HTMLElement)) return;
    const right = tab.offsetLeft + tab.offsetWidth;
    if (tab.offsetLeft < node.scrollLeft) node.scrollLeft = tab.offsetLeft;
    else if (right > node.scrollLeft + node.clientWidth) node.scrollLeft = right - node.clientWidth;
  }, [at]);

  if (items.length === 0) return null;
  const step = (way: number) => {
    const node = strip.current;
    if (node) node.scrollBy({ left: way * node.clientWidth * 0.8, behavior: 'smooth' });
  };
  const keyed = (event: KeyboardEvent) => {
    const last = items.length - 1;
    const to = { ArrowLeft: at === 0 ? last : at - 1, ArrowRight: at === last ? 0 : at + 1, Home: 0, End: last }[event.key];
    if (to === undefined) return;
    event.preventDefault();
    setChosen(to);
    const tab = strip.current?.children[to];
    if (tab instanceof HTMLElement) tab.focus();
  };

  return (
    <div className="tabs">
      <div className="tabs-bar">
        {label && <span className="label">{label}</span>}
        {reach !== FITS && <Step way={-1} enabled={reach.back} onClick={() => step(-1)} />}
        {/* `data-scroll` says this box shows what it clips, on a control that
            says so -- which is what excuses it from the "nothing is wider than
            its box" check in `shots.mjs`. */}
        <div
          className="tabs-strip"
          data-scroll
          ref={strip}
          onScroll={measure}
          onKeyDown={keyed}
          role="tablist"
          aria-label={label}
        >
          {items.map((item, position) => (
            <button
              key={item.key}
              type="button"
              className={`tab tab-${item.tone ?? 'plain'} ${position === at ? 'is-chosen' : ''}`}
              role="tab"
              id={`${id}-tab-${position}`}
              aria-selected={position === at}
              aria-controls={`${id}-panel`}
              tabIndex={position === at ? 0 : -1}
              onClick={() => setChosen(position)}
            >
              {item.label}
              {item.note && <span className="tab-note">{item.note}</span>}
            </button>
          ))}
        </div>
        {reach !== FITS && <Step way={1} enabled={reach.on} onClick={() => step(1)} />}
      </div>
      <div className="tabs-panel" role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${at}`}>
        {items[at]?.body}
      </div>
    </div>
  );
}

/**
 * How far the strip can still be scrolled, each way.
 *
 * `FITS` is one shared object so that `reach !== FITS` is the question "does
 * this strip scroll at all", which decides whether the chevrons take up room.
 * Reserving their width only while scrolling made the tabs jump sideways the
 * moment a reader touched them.
 */
interface Reach {
  back: boolean;
  on: boolean;
}

const FITS: Reach = { back: false, on: false };

/** One chevron. Disabled rather than hidden at the end, so the strip stays put. */
function Step({ way, enabled, onClick }: { way: 1 | -1; enabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`tabs-step tabs-step-${way === 1 ? 'on' : 'back'}`}
      disabled={!enabled}
      aria-label={way === 1 ? 'Scroll tabs right' : 'Scroll tabs left'}
      onClick={onClick}
    >
      <Chevron open={false} />
    </button>
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
  const [theme, setTheme] = useState<Theme>(storedTheme);

  // `index.html` applies the stored choice before the first paint; this keeps
  // the attribute and the store in step with the toggle after that.
  useEffect(() => {
    const root = window.document.documentElement;
    if (theme === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    try {
      if (theme === 'system') localStorage.removeItem('theme');
      else localStorage.setItem('theme', theme);
    } catch {
      // Storage refused (a file:// page, a private window): the choice lasts
      // until the page closes, which is all that can be offered.
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

/** The stored choice, if it is one; anything else in the store means none was made. */
function storedTheme(): Theme {
  try {
    const stored = localStorage.getItem('theme');
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  } catch {
    return 'system';
  }
}
