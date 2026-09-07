/**
 * The exact ratios `minimum`, `maximum` and `multipleOf` arrive as.
 *
 * The parser never passes a number through `float`, on either side of a
 * comparison: a numeric facet is a `Fraction` built from the raw scalar text,
 * so `multipleOf: 0.01` is `1/100` and not `0.01000000000000000020816...`.
 * That is why the tree carries the string `"1/100"` -- a JSON number could not
 * hold it, and rounding it in the emitter would throw away the exactness the
 * whole rule exists to keep.
 *
 * A reader did not write `1/100`, though. They wrote `0.01`, and that is what
 * this recovers -- **exactly**, by long division in `BigInt`, never by
 * `Number(n) / Number(d)`, which is the one line that would put the value back
 * through the float the parser spent its effort avoiding.
 *
 * A ratio is a terminating decimal exactly when its reduced denominator has no
 * prime factor but 2 and 5. Every decimal an author can write reduces to one,
 * so the other branch is unreachable from RAML source -- and it is here anyway,
 * because `1/3` printed as `0.333…` would be a lie of exactly the kind this
 * module exists to prevent, and printed as `0.333` a different one.
 */

export interface Rational {
  numerator: bigint;
  denominator: bigint;
}

/** `"1/100"`, `"-3/4"` or `"7"`. Anything else is not one. */
export function rationalOf(text: string): Rational | null {
  const match = /^\s*(-?\d+)(?:\s*\/\s*(\d+))?\s*$/.exec(text);
  if (!match) return null;
  const denominator = BigInt(match[2] ?? '1');
  if (denominator === 0n) return null;
  return { numerator: BigInt(match[1]!), denominator };
}

/**
 * The decimal, where there is an exact one; the ratio unchanged otherwise.
 *
 * `1/100` -> `0.01`, `5/2` -> `2.5`, `7/1` -> `7`, `1/3` -> `1/3`.
 */
export function decimalOf({ numerator, denominator }: Rational): string {
  const divisor = gcd(numerator < 0n ? -numerator : numerator, denominator);
  let n = numerator / divisor;
  const d = denominator / divisor;

  // Strip the factors a decimal can absorb. What is left decides it.
  let rest = d;
  let twos = 0;
  let fives = 0;
  while (rest % 2n === 0n) {
    rest /= 2n;
    twos += 1;
  }
  while (rest % 5n === 0n) {
    rest /= 5n;
    fives += 1;
  }
  if (rest !== 1n) return `${n}/${d}`;

  const places = Math.max(twos, fives);
  const negative = n < 0n;
  if (negative) n = -n;
  const scaled = ((n * 10n ** BigInt(places)) / d).toString().padStart(places + 1, '0');
  const whole = scaled.slice(0, scaled.length - places);
  const fraction = places === 0 ? '' : scaled.slice(scaled.length - places).replace(/0+$/, '');
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`;
}

/** The facets the tree carries as ratios (`tree.d.ts` says so on each). */
export const RATIO_FACETS: ReadonlySet<string> = new Set(['minimum', 'maximum', 'multipleOf']);

/** One of those, as a reader wrote it. Anything unrecognised passes through. */
export function showRatio(text: string): string {
  const rational = rationalOf(text);
  return rational === null ? text : decimalOf(rational);
}

function gcd(a: bigint, b: bigint): bigint {
  while (b !== 0n) [a, b] = [b, a % b];
  return a === 0n ? 1n : a;
}
