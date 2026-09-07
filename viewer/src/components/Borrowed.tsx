/**
 * Rows a table shows but did not declare.
 *
 * Two things reach an operation from outside it, and a caller has to send both:
 * what the chosen security scheme's `describedBy` adds, and what the base URI
 * asks for. Neither is the operation's own, and the model is right to keep them
 * apart -- `{tenant}` parameterises the base URI, not the path, and a scheme's
 * headers belong to the scheme.
 *
 * They are merged into the table they affect all the same, because a reader
 * building a request needs one list of what to send and not three sections to
 * assemble it from. Each borrowed row says where it came from, so merged is not
 * the same as indistinguishable.
 */

import { Lock } from './ui';

export interface Borrowed<T> {
  /** The rows, keyed as the table's own are. A declared name wins. */
  rows: Record<string, T>;
  /** What to print on the row. */
  label: string;
  /** The long form, for the row's tooltip. */
  title: string;
  /** A padlock, where the source is a security scheme. */
  secured?: boolean;
}

/** Where a row came from, when it was not the operation's own. */
export function From({ label, title, secured }: { label: string; title: string; secured?: boolean }) {
  return (
    <span className="from" title={title}>
      {secured && <Lock />}
      {label}
    </span>
  );
}

/** What a security scheme contributes, as a borrowing. */
export function fromScheme<T>(rows: Record<string, T> | undefined, scheme: string | undefined): Borrowed<T> | undefined {
  if (!rows || !scheme) return undefined;
  return { rows, label: scheme, title: `added by the ${scheme} security scheme`, secured: true };
}

/** What the base URI asks of every request, as a borrowing. */
export function fromBaseUri<T>(rows: Record<string, T> | undefined): Borrowed<T> | undefined {
  if (!rows || Object.keys(rows).length === 0) return undefined;
  return { rows, label: 'base URI', title: 'declared by baseUriParameters; every request to this API supplies it' };
}
