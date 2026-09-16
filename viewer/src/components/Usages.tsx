/**
 * Where a declaration is used, found by walking the tree for its address.
 *
 * A tree carries containment and drops the reverse direction, so this is a
 * walk rather than a lookup -- the join the graph exists to make cheap
 * (docs/16 § 4). At the size a browser holds a document, a walk is the right
 * trade; a consumer that wants it indexed reads `fastraml refs` instead.
 *
 * The walk follows the metamodel: descend containment, read a link, stop at a
 * recursion marker. A use can also sit in a named slot rather than a link --
 * a `securedBy` entry names its scheme by address -- so the marker's `head`
 * and a node's `declaration` are read as references too. What it does not do
 * is stringify the endpoint and search the text: an address inside an example
 * or a `discriminator_value` is data, not a use.
 */

import { Link } from 'react-router';
import { type Address, type Document, isRef, isRecursive } from '../model';
import { Section } from './ui';

/** Whether `node` uses `address`, descended by the three constructs. */
function uses(node: unknown, address: Address): boolean {
  if (Array.isArray(node)) return node.some((item) => uses(item, address));
  if (typeof node !== 'object' || node === null) return false;
  if (isRef(node)) return node.$ref === address;
  if (isRecursive(node)) return node.head.$ref === address;
  // A `securedBy` entry names its scheme by address rather than linking it.
  if ((node as { declaration?: Address | null }).declaration === address) return true;
  return Object.values(node).some((value) => uses(value, address));
}

export function Usages({ document, address }: { document: Document; address: Address | null }) {
  if (address === null) return null;
  const found = Object.keys(document.endpoints).filter((path) => uses(document.endpoints[path], address));
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
