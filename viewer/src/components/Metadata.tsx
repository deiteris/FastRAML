/**
 * What the author added that RAML's type vocabulary did not define.
 *
 * **A built-in facet constrains the data; an annotation and a custom facet
 * describe the declaration.** `maxLength 200` says what a payload may contain,
 * and `(deprecated): use PUT` and `stewardedBy: catalogue-team` say nothing a
 * request or a response has to satisfy. Rendered in the same band, in the same
 * chips, they read as two more constraints -- the one thing they are not.
 *
 * So they are drawn together, once, in a region of their own. And each row
 * carries the **type** it was declared with: an annotation has an
 * `annotationTypes:` entry and a custom facet has a `facets:` entry, both of
 * them type declarations, and neither of them was on the page. A value with no
 * type is author text that a reader cannot check against anything.
 *
 * One component and not one per site: `(deprecated)` on a type, a method, a
 * response and a security scheme is the same statement, and the tree gives all
 * four the same shape. Only a type has custom facets, so the other sites pass
 * annotations alone and the region is annotations alone.
 */

import { Link } from 'react-router';
import { type Applied, type Index, type Json, type Shape, facetDeclaration, labelOf } from '../model';
import { Code, oneLine } from './json';

export function Metadata({
  applied,
  facets,
  owner,
  index,
}: {
  applied?: Applied[];
  /** Values supplied for facets a supertype declared -- `shape.custom_facets`. */
  facets?: Record<string, Json>;
  /** Where to start looking for those facets' declarations. */
  owner?: Shape;
  index: Index;
}) {
  const supplied = Object.entries(facets ?? {});
  if ((applied ?? []).length === 0 && supplied.length === 0) return null;
  return (
    <div className="metadata">
      <span className="label">metadata</span>
      {(applied ?? []).map((one, at) => (
        <Annotation key={`a${at}`} applied={one} index={index} />
      ))}
      {supplied.map(([name, value]) => (
        <Facet key={`f${name}`} name={name} value={value} owner={owner} index={index} />
      ))}
    </div>
  );
}

/**
 * Annotations alone, for a site that has no facets of its own.
 *
 * Named for what the caller has rather than for what the region is: an
 * endpoint, an operation, a response and a scheme carry annotations and
 * nothing else, and `<Metadata applied=...>` at four sites read as though the
 * other half had been forgotten.
 */
export function Annotations({ applied, index }: { applied?: Applied[]; index: Index }) {
  return <Metadata applied={applied} index={index} />;
}

function Annotation({ applied, index }: { applied: Applied; index: Index }) {
  const entry = index.get(applied.type);
  const declared = index.shape(applied.type);
  return (
    <Row
      name={`(${applied.name})`}
      href={entry?.href}
      // The annotation type's own kind. `(rateLimit): { calls: 100 }` is an
      // object and `(deprecated): use PUT` is a string, and which one a reader
      // is looking at decided how to read the value.
      type={declared ? labelOf(declared, index) : undefined}
      kind="annotation"
      value={applied.value}
    />
  );
}

function Facet({
  name,
  value,
  owner,
  index,
}: {
  name: string;
  value: Json;
  owner?: Shape;
  index: Index;
}) {
  const found = owner ? facetDeclaration(owner, name, index) : null;
  const by = found ? index.get(found.by.id) : undefined;
  return (
    <Row
      name={name}
      type={found ? labelOf(found.declared, index) : undefined}
      kind="facet"
      // Where the facet was declared, which is never this type: a `facets:`
      // block declares what *subtypes* must supply (docs/10 section 4), so the
      // rule that makes this value legal is written on a page the reader is
      // not on.
      from={by ? { label: by.name, href: by.href } : undefined}
      value={value}
    />
  );
}

/**
 * One entry: what it is called, what it was declared as, and what it says.
 *
 * Head line then value, which is the treatment an attribute already has -- the
 * name and its type on one line, the content under it. A value is author text
 * of any length, and trailing it onto the head line wrapped a regex or a
 * paragraph into the column the names are scanned in.
 */
function Row({
  name,
  href,
  type,
  kind,
  from,
  value,
}: {
  name: string;
  href?: string;
  type?: string;
  kind: 'annotation' | 'facet';
  from?: { label: string; href: string };
  value: Json;
}) {
  return (
    <div className="meta">
      <div className="meta-head">
        {href ? (
          <Link to={href} className="meta-name is-link">
            {name}
          </Link>
        ) : (
          <code className="meta-name">{name}</code>
        )}
        {type && <span className="meta-type">{type}</span>}
        <span className="meta-kind">{kind}</span>
        {from && (
          <span className="meta-from">
            declared by{' '}
            <Link to={from.href} className="typelink">
              {from.label}
            </Link>
          </span>
        )}
      </div>
      {/* `null` is what an annotation with no value projects, and it is not a
          value of `null`: `(internal)` on its own is the whole statement. */}
      {value !== null &&
        // A structured value is read the way every other structured value on
        // the page is read. `(rateLimit): {perMinute: 60}` on one line is the
        // compact JSON `{"perMinute":60}` -- legible at two keys and not at
        // five, which is the size an annotation type with properties reaches.
        (typeof value === 'object' ? (
          <Code>{value}</Code>
        ) : (
          <div className="meta-value">{oneLine(value)}</div>
        ))}
    </div>
  );
}
