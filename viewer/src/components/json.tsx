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
import { CopyButton } from './ui';

/**
 * A value or a snippet as a code block, with a button that copies it.
 *
 * A block of more than one line has one: an example is the thing a reader most
 * often takes away from a page, and selecting a forty-line block by hand drags
 * the page with it. A one-line value -- `90`, `"draft"` -- is selected with a
 * double click, and on a touch screen, where the button never hides, a Copy
 * beside every default and annotation value was more button than value.
 * The copy is the text as shown -- exact integers included -- not the markup.
 */
export function Code({ children, language }: { children: Json; language?: string }) {
  const text = typeof children === 'string' ? children : stringify(children, 2);
  const highlighted = highlightCode(text, language ?? (typeof children === 'string' ? undefined : 'json'));
  const block = highlighted ? (
    <pre className="code" data-language={highlighted.language}>
      <code className={`hljs language-${highlighted.language}`} dangerouslySetInnerHTML={{ __html: highlighted.html }} />
    </pre>
  ) : (
    <pre className="code">{text}</pre>
  );
  if (!text.includes('\n')) return block;
  return (
    <div className="code-block">
      {block}
      <CopyButton text={text} />
    </div>
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
 * The single way a value reaches a line of the page. Numeric bounds need no
 * special case: the tree already carries them as exact decimal strings
 * (docs/16 § 6.2).
 */
export function oneLine(value: Json): string {
  if (typeof value === 'string') return value;
  if (value === null) return 'null';
  return stringify(value);
}
