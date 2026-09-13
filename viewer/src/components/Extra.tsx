/**
 * The extension points: annotations, and the facets an author declared.
 *
 * **Extra, not metadata.** Both exist so that a processor other than this one
 * can be told something RAML has no word for -- a code generator reading
 * `(goLang.package)`, a gateway reading `(rateLimit)`, a catalogue reading
 * `stewardedBy`. What they mean is the consumer's to decide, and this viewer is
 * one consumer among them: it can say what was written, what type it was
 * declared with, and where the declaration is, and it cannot say what any of it
 * does. A region that claimed otherwise would be inventing a reading.
 *
 * Which is why they are drawn apart from the facets above rather than among
 * them. **A built-in facet constrains the data; these describe the
 * declaration.** `maxLength 200` says what a payload may contain; `(deprecated):
 * use PUT` and `stewardedBy: catalogue-team` say nothing a request or a response
 * has to satisfy. In the same band, in the same chips, they read as two more
 * constraints -- the one thing they are not.
 *
 * Each row carries the **type** it was declared with, because both halves of
 * the extension point are typed: an annotation has an `annotationTypes:` entry
 * and a custom facet has a `facets:` entry, and neither was on the page. A value
 * with no type is author text that a reader cannot check against anything, and
 * the processor meant to act on it is holding a contract the reader cannot see.
 *
 * **Both halves of a custom facet live here**, the `facets:` block that declares
 * one and the values a subtype supplies for it. Drawn as an attribute list of
 * its own, a `facets:` block sat beside the type's properties in the same
 * treatment, and read as more of them -- when what a payload must carry and what
 * a *subtype* must supply are different claims about different things. Inside
 * this region it is bounded by the one edge that says so.
 *
 * One component and not one per site: `(deprecated)` on a type, a method, a
 * response and a security scheme is the same statement, and the tree gives all
 * four the same shape. Only a type has custom facets, so the other sites pass
 * annotations alone and the region is annotations alone.
 */

import { Link } from 'react-router';
import { type Applied, type Index, type Json, type Shape, facetDeclaration, labelOf } from '../model';
import { Code } from './json';

export function Extra({
  applied,
  facets,
  declares,
  owner,
  index,
}: {
  applied?: Applied[];
  /** Values supplied for facets a supertype declared -- `shape.custom_facets`. */
  facets?: Record<string, Json>;
  /**
   * A `facets:` block, already rendered. Passed in rather than built here so
   * that a declaration keeps the row treatment every other declaration has,
   * whose component lives beside the rest of them in `Shape`.
   */
  declares?: React.ReactNode;
  /** Where to start looking for those facets' declarations. */
  owner?: Shape;
  index: Index;
}) {
  const supplied = Object.entries(facets ?? {});
  if ((applied ?? []).length === 0 && supplied.length === 0 && !declares) return null;
  return (
    <div className="extra">
      <span className="label">extra</span>
      {(applied ?? []).map((one, at) => (
        <Annotation key={`a${at}`} applied={one} index={index} />
      ))}
      {supplied.map(([name, value]) => (
        <Facet key={`f${name}`} name={name} value={value} owner={owner} index={index} />
      ))}
      {declares}
    </div>
  );
}

/**
 * Annotations alone, for a site that has no facets of its own.
 *
 * Named for what the caller has rather than for what the region is: an
 * endpoint, an operation, a response and a scheme carry annotations and
 * nothing else, and `<Extra applied=...>` at four sites read as though the
 * other half had been forgotten.
 */
export function Annotations({ applied, index }: { applied?: Applied[]; index: Index }) {
  return <Extra applied={applied} index={index} />;
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
    <div className="extra-item">
      <div className="extra-head">
        {href ? (
          <Link to={href} className="extra-name is-link">
            {name}
          </Link>
        ) : (
          <code className="extra-name">{name}</code>
        )}
        {type && <span className="extra-type">{type}</span>}
        <span className="extra-kind">{kind}</span>
        {from && (
          <span className="extra-from">
            declared by{' '}
            <Link to={from.href} className="typelink">
              {from.label}
            </Link>
          </span>
        )}
      </div>
      {/* A code block, the way `default:` and `example:` are, and for the same
          reason: all three are a **value of a declared type**, and the block is
          what says so. `90` as running text is a word; in a block it is the
          integer the `facets:` entry demanded.

          One form for every type, not a block for the structured ones and text
          for the rest. A reader who has to notice which arrived is being asked
          to infer the type from the presentation, which is the thing the row
          above states outright.

          `null` is what an annotation with no value projects, and it is not a
          value of `null`: `(internal)` on its own is the whole statement. */}
      {value !== null && <Code>{value}</Code>}
    </div>
  );
}
