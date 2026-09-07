/**
 * Where a declaration is used, found by scanning for its address.
 *
 * A tree carries containment and drops the reverse direction, so this is a scan
 * rather than a lookup -- the join the graph exists to make cheap (docs/16 § 4).
 * At the size a browser holds a document, a scan is the right trade; a consumer
 * that wants it indexed reads `pyraml refs` instead.
 */

import { Link } from 'react-router';
import type { Address, Document } from '../model';
import { Section } from './ui';

export function Usages({ document, address }: { document: Document; address: Address | null }) {
  if (address === null) return null;
  const found: string[] = [];
  for (const [path, endpoint] of Object.entries(document.endpoints)) {
    if (JSON.stringify(endpoint).includes(JSON.stringify(address))) found.push(path);
  }
  if (found.length === 0) return null;
  return (
    <Section title={`Used by ${found.length} resource${found.length === 1 ? '' : 's'}`}>
      <ul className="usages">
        {found.map((path) => (
          <li key={path}>
            <Link to={`/endpoints/${encodeURIComponent(path)}`}>
              <code>{path}</code>
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}
