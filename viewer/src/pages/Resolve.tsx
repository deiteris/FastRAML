/**
 * `/n/:address` -- resolve any address to the page that shows it.
 *
 * The addresses are the same ones `fastraml graph` prints, so an address copied
 * out of a query result opens here. That join is what docs/16 § 3.1 is for, and
 * it costs one route.
 */

import { Link, useParams } from 'react-router';
import { Empty } from '../components/ui';
import type { Props } from './props';

export function ResolveAddress({ index }: Props) {
  const { address } = useParams();
  const entry = index.get(decodeURIComponent(address ?? ''));
  if (!entry) {
    return (
      <Empty>
        Nothing at <code>{address}</code>. Only declarations and resources have a page; a nested shape is shown where it
        sits.
      </Empty>
    );
  }
  return (
    <Empty>
      <Link to={entry.href}>Go to {entry.name}</Link>
    </Empty>
  );
}

export function NotFound() {
  return (
    <Empty>
      No such page. <Link to="/">Back to the overview.</Link>
    </Empty>
  );
}
