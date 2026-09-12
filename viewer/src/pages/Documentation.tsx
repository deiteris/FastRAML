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
import { Prose, ProseInline } from '../components/markdown';
import { Empty } from '../components/ui';
import type { Props } from './props';

export function DocumentationPage({ document }: Props) {
  const { at } = useParams();
  const items = document.entry_point?.documentation ?? [];
  const item = items[Number(at)];
  if (!item) return <Empty>No documentation item {at} in this document.</Empty>;
  // No subtitle. A page that says `documentation` under its own title is
  // labelling itself for nobody: the reader arrived from the Documentation
  // section of the nav, which is still showing this item as the current one.
  return (
    <article className="reading">
      <h1>{item.title}</h1>
      <Prose>{item.content}</Prose>
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
            {/* `ProseInline` takes the first paragraph itself. */}
            <ProseInline className="gloss">{item.content}</ProseInline>
          </li>
        ))}
      </ul>
    </article>
  );
}
