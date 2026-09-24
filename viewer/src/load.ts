/**
 * Getting a document in.
 *
 * One way: `api.json` beside the bundle.
 *
 * **There is deliberately no `?src=`.** Accepting any URL would let a crafted
 * link render someone else's document under this origin, with nothing on the
 * page saying so, and would make every host that mounts this bundle compose a
 * query string. A host that wants its own document served here serves it at
 * `api.json`.
 *
 * Through `parse` and not `JSON.parse`, so an example carrying an integer
 * larger than a double reaches the page as the author wrote it (`numbers.ts`).
 */

import { parse } from './numbers';
import { Tree, UnreadableTree } from './walk';
import type { Document } from './tree';

export const DEFAULT_SOURCE = 'api.json';

export async function loadDocument(source: string): Promise<Document> {
  const response = await fetch(source);
  if (!response.ok) throw new Error(`${source}: ${response.status} ${response.statusText}`);
  return validate(parse(await response.text()));
}

/**
 * Enough of a check to give a useful message.
 *
 * Not a schema: the one mistake worth catching by name is pointing this at a
 * `fastraml graph --format json` output, which is also JSON, also has nodes, and
 * would otherwise render as an empty document with no explanation.
 */
function validate(value: unknown): Document {
  if (typeof value !== 'object' || value === null) throw new Error('not a JSON object');
  const document = value as Partial<Document> & { nodes?: unknown };
  if (document.nodes !== undefined && document.endpoints === undefined) {
    throw new Error('this looks like `fastraml graph --format json`; this app reads `fastraml tree` output');
  }
  // The envelope check is the contract's, so `Tree.of` owns it and this only
  // translates the failure into the message this app shows. A second copy here
  // was a second place for the three constants to go stale.
  try {
    Tree.check(value);
  } catch (error) {
    throw new Error(error instanceof UnreadableTree ? error.message : String(error));
  }
  for (const key of ['types', 'endpoints', 'annotation_types', 'security_schemes'] as const) {
    if (typeof document[key] !== 'object' || document[key] === null) {
      throw new Error(`missing "${key}": expected the output of \`fastraml tree FILE\``);
    }
  }
  return document as Document;
}
