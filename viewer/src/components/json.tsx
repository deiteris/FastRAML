/**
 * Rendering a value the tree carries.
 *
 * Everything here takes `Json` -- the generated type, the same one `tree.d.ts`
 * gives every value position in the contract. It took `unknown` first, which
 * compiles and says nothing: a facet's value, an example, an annotation's
 * argument and a scheme's setting are all `Json` and the checker had no way to
 * know it, so every call site cast or stringified its way out.
 */

import type { Json } from '../model';
import { RATIO_FACETS, showRatio } from '../rational';

export function Code({ children }: { children: Json }) {
  const text = typeof children === 'string' ? children : JSON.stringify(children, null, 2);
  return <pre className="code">{text}</pre>;
}

export function Labelled({ label, value }: { label: string; value: Json }) {
  return (
    <div className="labelled">
      <span className="label">{label}</span>
      <Code>{value}</Code>
    </div>
  );
}

/** A value on one line: a string as itself, anything structured as compact JSON. */
export function oneLine(value: Json): string {
  if (typeof value === 'string') return value;
  if (value === null) return 'null';
  return JSON.stringify(value);
}

/**
 * A facet's value, with the three that are exact ratios read back as decimals.
 *
 * `multipleOf 1/100` is what the tree carries and is faithful to the parser;
 * it is not what the author wrote and not what a reader is checking against.
 */
export function facetValue(name: string, value: Json): string {
  if (RATIO_FACETS.has(name) && typeof value === 'string') return showRatio(value);
  return oneLine(value);
}
