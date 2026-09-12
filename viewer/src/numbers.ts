/**
 * Numbers this runtime cannot hold, kept as the text the document carried.
 *
 * A *bound* -- `minimum`, `maximum`, `multipleOf` -- arrives as an exact decimal
 * string and needs nothing here; the emitter settled that, for the reason
 * `tree.d.ts` gives. **Example and default data is different**: it is the
 * author's payload, and an integer in a payload has to stay an integer, so the
 * tree emits it as a JSON number and `JSON.parse` rounds anything past 2^53 to
 * the nearest double -- `9223372036854775807` reads back as `...808`, an ID no
 * caller can use, in the one place a reader copies from.
 *
 * So the document is parsed with the literal's own source text in hand, and a
 * literal a double cannot hold is boxed rather than rounded. `stringify` writes
 * one back out unquoted, which is why it exists instead of `JSON.stringify`.
 *
 * An `Exact` therefore inhabits `Json` positions at runtime, and the generated
 * contract cannot say so. It is safe because every value position in this app
 * is *displayed* and nothing computes with one: the two functions below are the
 * only readers, and `oneLine` and `Code` are the only ways a value reaches the
 * page.
 */

export interface Exact {
  readonly exact: string;
}

export function isExact(value: unknown): value is Exact {
  return typeof value === 'object' && value !== null && typeof (value as Exact).exact === 'string';
}

/**
 * Whether a literal says something the double it parsed to does not.
 *
 * Two cases, and not one test for both. An **integer** literal past the safe
 * range is exactly decidable and is the case that matters -- an int64 ID. A
 * **fractional** literal is approximate by nature, and `String(value)` is
 * already the shortest text that round-trips to the same double, so the only
 * loss worth reporting is significant digits the format cannot hold at all.
 */
function lossy(source: string, value: number): boolean {
  if (/^-?\d+$/.test(source)) return !Number.isSafeInteger(value);
  const significant = source.replace(/[eE].*$/, '').replace(/[-.]/g, '').replace(/^0+/, '');
  return significant.length > DOUBLE_DIGITS;
}

/** A double holds 17 significant decimal digits, and no more. */
const DOUBLE_DIGITS = 17;

interface Context {
  source?: string;
}

/**
 * `JSON.parse`, keeping a literal a double cannot hold.
 *
 * The third argument to a reviver is the literal's own source text (ES2025). It
 * is feature-detected rather than assumed: where a runtime does not supply it
 * this is `JSON.parse`, with the rounding that implies, and nothing else in the
 * app changes.
 */
export function parse(text: string): unknown {
  if (!SOURCE_ACCESS) return JSON.parse(text);
  return JSON.parse(text, function (_key: string, value: unknown, context?: Context) {
    const source = context?.source;
    if (typeof value !== 'number' || source === undefined) return value;
    return lossy(source, value) ? { exact: source } : value;
  });
}

const SOURCE_ACCESS: boolean = (() => {
  let seen = false;
  JSON.parse('1', function (_key: string, value: unknown, context?: Context) {
    seen = typeof context?.source === 'string';
    return value;
  });
  return seen;
})();

/**
 * `JSON.stringify` that writes an `Exact` as the number it is.
 *
 * Hand-rolled rather than `JSON.stringify` with a replacer and a sentinel: a
 * replacer can only return a JSON value, so a boxed number comes back quoted,
 * and unquoting it afterwards means a marker string that author data could also
 * contain. The output matches `JSON.stringify` exactly for everything else,
 * which `smoke` checks against the document itself rather than against examples
 * written here.
 */
export function stringify(value: unknown, indent = 0): string {
  return write(value, indent, '');
}

function write(value: unknown, indent: number, pad: string): string {
  if (isExact(value)) return value.exact;
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  const inner = pad + ' '.repeat(indent);
  const gap = indent > 0 ? ' ' : '';
  const parts = Array.isArray(value)
    ? value.map((item) => write(item, indent, inner))
    : Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .map(([key, item]) => `${JSON.stringify(key)}:${gap}${write(item, indent, inner)}`);
  const [open, close] = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
  if (parts.length === 0) return open + close;
  if (indent === 0) return `${open}${parts.join(',')}${close}`;
  return `${open}\n${inner}${parts.join(`,\n${inner}`)}\n${pad}${close}`;
}
