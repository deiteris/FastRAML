/**
 * A named set of inputs: URI parameters, headers, query parameters.
 *
 * All three are `Parameter` in the tree and all three are the same table here.
 * The rows are `Attribute`, the same component a type's properties use, so a
 * header whose type is an object opens the way a property does.
 */

import type { Index, Parameter } from '../model';
import type { Borrowed } from './Borrowed';
import { Attribute } from './Shape';

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
