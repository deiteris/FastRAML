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
