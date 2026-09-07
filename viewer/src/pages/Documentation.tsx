/**
 * One `documentation:` item.
 *
 * A page of its own rather than a disclosure on the overview. These are the
 * prose an author writes when the reference is not enough -- authentication
 * walkthroughs, pagination, error conventions -- and they are the one part of a
 * document that is meant to be *read* rather than looked up. Stacked under the
 * overview they were four collapsed rows below the type counts, and a reader
 * arriving from the nav had no way to link anyone to one of them.
 *
 * Keyed by position, because a documentation item is the one thing in the tree
 * with no address: it is a title and a body, and two may share a title.
 */

import { Link, useParams } from 'react-router';
import { Empty, Prose } from '../components/ui';
import type { Props } from './props';

export function DocumentationPage({ document }: Props) {
  const { at } = useParams();
  const items = document.entry_point?.documentation ?? [];
  const item = items[Number(at)];
  if (!item) return <Empty>No documentation item {at} in this document.</Empty>;
  return (
    <article>
      <h1>{item.title}</h1>
      <p className="subtitle">documentation</p>
      {/* Verbatim, like every other description here: RAML prose is Markdown
          and rendering it means a Markdown dependency and its injection
          surface, for a view whose subject is the model. */}
      <pre className="prose-block">{item.content}</pre>
    </article>
  );
}

export function DocumentationList({ document }: Props) {
  const items = document.entry_point?.documentation ?? [];
  if (items.length === 0) return <Empty>This document has no documentation items.</Empty>;
  return (
    <article>
      <h1>Documentation</h1>
      <ul className="usages">
        {items.map((item, at) => (
          <li key={at}>
            <Link to={`/documentation/${at}`}>{item.title}</Link>
            <Prose>{first(item.content)}</Prose>
          </li>
        ))}
      </ul>
    </article>
  );
}

/** The first line, as a gloss. These run to paragraphs. */
function first(content: string): string {
  return content.split(/\r?\n/).find((line) => line.trim() !== '') ?? '';
}
