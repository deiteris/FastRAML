/**
 * The search dialog: one large field, and every match grouped under what it is.
 *
 * A modal `<dialog>` and not a positioned `<div>`: `showModal` puts it in the
 * top layer and makes the page behind it inert, so focus cannot wander out.
 * Escape is handled twice: `cancel` when focus is on the Close button, the
 * field's own key handler otherwise. Focus is returned by hand, because the
 * dialog is unmounted rather than closed.
 *
 * The field and the list are the WAI-ARIA combobox pattern: focus stays in the
 * field, the arrow keys move a highlight through the options, and
 * `aria-activedescendant` tells assistive technology which one it is on.
 * Groups are `role="group"`, labelled by their heading, so a screen reader
 * says "Types" before `Book`.
 */

import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import type { Document, Index } from '../model';
import { type Runs, type SearchEntry, searchIndexOf } from '../search';
import { CrossIcon, Verb } from './ui';

export function SearchDialog({
  document,
  index,
  query,
  onQuery,
  onClose,
}: {
  document: Document;
  index: Index;
  query: string;
  onQuery: (query: string) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const id = useId();
  const groups = useMemo(() => searchIndexOf(document, index).find(query), [document, index, query]);
  const results = groups.flatMap((group) => group.results);
  const [active, setActive] = useState(0);
  const at = Math.min(active, Math.max(results.length - 1, 0));
  const optionId = (position: number) => `${id}-option-${position}`;

  useEffect(() => {
    const node = dialog.current;
    const before = window.document.activeElement;
    node?.showModal();
    // Selected, so a reader who reopens it can refine the last query or type
    // over it without clearing it first.
    field.current?.select();
    return () => {
      node?.close();
      if (before instanceof HTMLElement) before.focus({ preventScroll: true });
    };
  }, []);

  // Keep the highlighted option in view. By hand rather than `scrollIntoView`,
  // which also scrolls every ancestor.
  useEffect(() => {
    const box = list.current;
    const option = box?.querySelector(`#${CSS.escape(optionId(at))}`);
    if (!box || !(option instanceof HTMLElement)) return;
    const top = option.offsetTop - box.offsetTop;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (top + option.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top + option.offsetHeight - box.clientHeight;
  });

  const choose = (entry: SearchEntry | undefined) => {
    if (!entry) return;
    onClose();
    navigate(entry.href);
  };

  const keyed = (event: KeyboardEvent) => {
    // Taken before the field sees it: a search field spends its first Escape
    // clearing itself, so closing took two presses and lost the query.
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    const last = results.length - 1;
    if (last < 0) return;
    const to = { ArrowDown: at === last ? 0 : at + 1, ArrowUp: at === 0 ? last : at - 1 }[event.key];
    if (to !== undefined) {
      event.preventDefault();
      setActive(to);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      choose(results[at]?.entry);
    }
  };

  let position = 0;
  return (
    // A click on the backdrop lands on the dialog itself; one inside lands on
    // its content, which fills it.
    <dialog
      ref={dialog}
      className="search"
      aria-label="Search the API"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => event.target === dialog.current && onClose()}
    >
      <div className="search-body">
        <div className="search-head">
          <input
            ref={field}
            className="search-field"
            type="search"
            placeholder="Search endpoints, operations, types, documentation…"
            value={query}
            onChange={(event) => {
              onQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={keyed}
            role="combobox"
            aria-expanded={results.length > 0}
            aria-controls={`${id}-results`}
            aria-activedescendant={results.length > 0 ? optionId(at) : undefined}
            aria-autocomplete="list"
            aria-label="Search"
            autoComplete="off"
            spellCheck={false}
          />
          {/* Ours and not the browser's, which is drawn in its own colours
              whatever the theme; the stylesheet hides that one. */}
          {query !== '' && (
            <button
              type="button"
              className="search-clear"
              aria-label="Clear search"
              onClick={() => {
                onQuery('');
                setActive(0);
                field.current?.focus();
              }}
            >
              <CrossIcon />
            </button>
          )}
          {/* On a phone the dialog is the whole screen and there is no Escape
              key, so without this the only way out was choosing a result. */}
          <button type="button" className="plain-button" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="visually-hidden" role="status">
          {query.trim() === '' ? '' : `${results.length} ${results.length === 1 ? 'result' : 'results'}`}
        </p>
        <div ref={list} className="search-results" id={`${id}-results`} role="listbox" aria-label="Results">
          {groups.map((group) => (
            <div key={group.category} role="group" aria-labelledby={`${id}-${group.category}`}>
              <div className="search-group" id={`${id}-${group.category}`} role="presentation">
                {group.label}
              </div>
              {group.results.map((result) => {
                const mine = position++;
                const { entry } = result;
                return (
                  <div
                    key={`${entry.category} ${entry.href}`}
                    id={optionId(mine)}
                    className={`search-option ${mine === at ? 'is-active' : ''}`}
                    role="option"
                    aria-selected={mine === at}
                    // Not a focus change: focus stays in the field.
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseMove={() => mine !== at && setActive(mine)}
                    onClick={() => choose(entry)}
                  >
                    <div className="search-line">
                      {entry.method && <Verb method={entry.method} />}
                      <span className={`search-title ${entry.category === 'documentation' ? '' : 'mono'}`}>
                        <Marked runs={result.title} />
                      </span>
                      {result.detail && (
                        <span className="search-detail">
                          <Marked runs={result.detail} />
                        </span>
                      )}
                      {entry.file && <span className="search-file">{entry.file}</span>}
                    </div>
                    {result.excerpt && (
                      <div className="search-excerpt">
                        <Marked runs={result.excerpt} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        {query.trim() !== '' && results.length === 0 && <p className="search-none">Nothing matches “{query.trim()}”.</p>}
        <p className="search-keys" aria-hidden="true">
          <kbd>↑</kbd> <kbd>↓</kbd> to move, <kbd>Enter</kbd> to open, <kbd>Esc</kbd> to close
        </p>
      </div>
    </dialog>
  );
}

function Marked({ runs }: { runs: Runs }) {
  return runs.map(([text, marked], at) => (marked ? <mark key={at}>{text}</mark> : text));
}

/**
 * What opens the dialog from the nav: a control drawn as the field it opens.
 *
 * A button and not an input, because typing into a field that then jumps to a
 * dialog moves the caret out from under the reader.
 */
export function SearchButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className="search-open" onClick={onOpen} aria-keyshortcuts="/ Control+K" aria-haspopup="dialog">
      <span>Search…</span>
      <kbd>/</kbd>
    </button>
  );
}
