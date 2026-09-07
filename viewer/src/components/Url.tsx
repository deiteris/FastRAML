/**
 * The address a caller actually uses: the base URI, then the path.
 *
 * A path on its own is not something anyone can call. The two are one string
 * and are styled as one, with the base dimmed -- the path is what distinguishes
 * this page from every other, and the base is what makes it a URL.
 */

import { type EntryPoint, baseUriOf, schemeOf } from '../model';

export function Url({ api, path, protocols }: { api: EntryPoint | null; path: string; protocols?: string[] }) {
  const base = baseUriOf(api);
  // A method-level `protocols:` narrower than the base URI's changes the
  // address for that method alone, so it belongs in the address.
  const scheme = schemeOf(protocols, base);
  const shown = scheme ? base.replace(/^[a-z][a-z0-9+.-]*:/i, `${scheme}:`) : base;
  return (
    <code className="url">
      <span className="url-base">{shown}</span>
      {path}
    </code>
  );
}
