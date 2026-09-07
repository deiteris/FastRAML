/**
 * A named set of inputs: URI parameters, headers, query parameters.
 *
 * All three are `Parameter` in the tree and all three are the same table here.
 * The rows are `Attribute`, the same component a type's properties use, so a
 * header whose type is an object opens the way a property does.
 */

import { Link } from 'react-router';
import { type Index, type Ref, type Shape, isRef } from '../model';
import type { Borrowed } from './Borrowed';
import { Attribute, ShapeView } from './Shape';
import type { Parameter } from '../model';

export function ParameterTable({
  title,
  parameters,
  borrowed,
  index,
}: {
  title: string;
  parameters?: Record<string, Parameter>;
  /** What reaches this table from outside -- see `Borrowed`. */
  borrowed?: Borrowed<Parameter>;
  index: Index;
}) {
  const own = Object.entries(parameters ?? {});
  // A declared name wins: a resource that names `{tenant}` itself has said
  // something about it that the base URI's declaration does not.
  const extra = Object.entries(borrowed?.rows ?? {}).filter(([name]) => !(name in (parameters ?? {})));
  if (own.length + extra.length === 0) return null;
  return (
    <section className="parameters">
      <h4>{title}</h4>
      <div className="attributes">
        {own.map(([name, parameter]) => (
          <Attribute key={name} name={name} property={parameter} index={index} />
        ))}
        {extra.map(([name, parameter]) => (
          <Attribute key={name} name={name} property={parameter} index={index} from={borrowed} />
        ))}
      </div>
    </section>
  );
}

/**
 * `queryString:` -- the whole query as one type, RAML's alternative to
 * `queryParameters:` and never allowed beside it.
 *
 * Expanded, not behind a link. It is a parameter set like the table above, and
 * rendering it as an ordinary reference put every parameter of the request
 * behind "Show child attributes" -- one click to see what the section is for,
 * where the operation's other inputs are simply listed.
 *
 * The link to the declaration stays, because the type has a page of its own.
 */
export function QueryString({ shape, index }: { shape: Shape | Ref | null | undefined; index: Index }) {
  if (shape === null || shape === undefined) return null;
  const entry = isRef(shape) ? index.get(shape.$ref) : undefined;
  const target = isRef(shape) ? index.shape(shape.$ref) : shape;
  return (
    <section className="parameters">
      <h4>Query string</h4>
      {entry && (
        <div className="shape-line">
          <span className="label">type</span>
          <Link to={entry.href} className="typelink">
            {entry.name}
          </Link>
        </div>
      )}
      <ShapeView shape={target ?? shape} index={index} hideType />
    </section>
  );
}
