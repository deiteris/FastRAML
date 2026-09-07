/**
 * Applied annotations, wherever they were applied.
 *
 * One component and not one per site: `(deprecated)` on a type, a method, a
 * response and a security scheme is the same statement, and the tree gives all
 * four the same shape.
 */

import { Link } from 'react-router';
import type { Applied, Index } from '../model';
import { Chip } from './ui';
import { oneLine } from './json';

export function Annotations({ applied, index }: { applied?: Applied[]; index: Index }) {
  if (!applied || applied.length === 0) return null;
  return (
    <div className="shape-line">
      {applied.map((one, at) => {
        const entry = index.get(one.type);
        return (
          <Chip key={at} tone="warn">
            {entry ? (
              <Link to={entry.href} className="typelink">
                ({one.name})
              </Link>
            ) : (
              <span>({one.name})</span>
            )}
            {one.value !== null && <span className="facet-value">{oneLine(one.value)}</span>}
          </Chip>
        );
      })}
    </div>
  );
}
