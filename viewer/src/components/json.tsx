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
import { stringify } from '../numbers';
import { highlightCode } from './highlighting';

export function Code({ children, language }: { children: Json; language?: string }) {
  const text = typeof children === 'string' ? children : stringify(children, 2);
  const highlighted = highlightCode(text, language ?? (typeof children === 'string' ? undefined : 'json'));
  if (!highlighted) return <pre className="code">{text}</pre>;
  return (
    <pre className="code" data-language={highlighted.language}>
      <code className={`hljs language-${highlighted.language}`} dangerouslySetInnerHTML={{ __html: highlighted.html }} />
    </pre>
  );
}

export function Labelled({ label, value }: { label: string; value: Json }) {
  return (
    <div className="labelled">
      <span className="label">{label}</span>
      <Code>{value}</Code>
    </div>
  );
}

/**
 * A value on one line: a string as itself, anything structured as compact JSON.
 *
 * The single way a value reaches a line of the page. It used to have a sibling,
 * `facetValue`, which turned the three bound facets from the exact ratios the
 * tree carried -- `multipleOf 1/100` -- into the decimals their authors wrote.
 * The emitter does that now, where the exact arithmetic already lived, so every
 * consumer gets a readable bound instead of each writing its own long division.
 */
export function oneLine(value: Json): string {
  if (typeof value === 'string') return value;
  if (value === null) return 'null';
  return stringify(value);
}
