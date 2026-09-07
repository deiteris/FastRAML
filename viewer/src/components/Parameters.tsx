/**
 * A named set of inputs: URI parameters, headers, query parameters.
 *
 * All four are `Parameter` in the tree and all four are the same table here.
 * The rows are `Attribute`, the same component a type's properties use, so a
 * header whose type is an object opens the way a property does.
 */

import type { Index, Parameter } from '../model';
import { Attribute } from './Shape';

export function ParameterTable({
  title,
  parameters,
  added,
  from,
  index,
}: {
  title: string;
  parameters?: Record<string, Parameter>;
  /**
   * What the chosen security scheme contributes here.
   *
   * Merged into this table rather than given one of its own: a caller building
   * a request needs one list of what to send, and a section apart made them
   * assemble it from two places. Each borrowed row says where it came from, so
   * merged is not the same as indistinguishable -- a reader can still see what
   * would change if the scheme did.
   */
  added?: Record<string, Parameter>;
  from?: string;
  index: Index;
}) {
  const own = Object.entries(parameters ?? {});
  const extra = Object.entries(added ?? {}).filter(([name]) => !(name in (parameters ?? {})));
  if (own.length + extra.length === 0) return null;
  return (
    <section className="parameters">
      <h4>{title}</h4>
      <div className="attributes">
        {own.map(([name, parameter]) => (
          <Attribute key={name} name={name} property={parameter} index={index} />
        ))}
        {extra.map(([name, parameter]) => (
          <Attribute key={name} name={name} property={parameter} index={index} from={from} />
        ))}
      </div>
    </section>
  );
}
