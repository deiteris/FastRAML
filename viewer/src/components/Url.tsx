/**
 * The address a caller actually uses: the base URI, then the path.
 *
 * A path on its own is not something anyone can call. The two are one string
 * and are styled as one, with the base dimmed -- the path is what distinguishes
 * this page from every other, and the base is what makes it a URL.
 *
 * A method-level `protocols:` narrower than the base URI's changes the address
 * for that method alone, so it belongs in the address (`baseFor`).
 */

import { type EntryPoint, baseFor } from '../model';

export function Url({ api, path, protocols }: { api: EntryPoint | null; path: string; protocols?: string[] }) {
  return (
    <code className="url">
      <span className="url-base">{baseFor(api, protocols)}</span>
      {path}
    </code>
  );
}
