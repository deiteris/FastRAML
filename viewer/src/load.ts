/**
 * Getting a document in.
 *
 * Three ways, because the app has three uses: a bundled `api.json` for a build
 * someone is handed, `?src=` for a document served alongside it, and a file
 * picker for whatever `pyraml tree` just printed. All three end at the same
 * parsed value; nothing downstream knows which was used.
 *
 * Through `parse` and not `JSON.parse`, so an example carrying an integer
 * larger than a double reaches the page as the author wrote it (`numbers.ts`).
 */

import { parse } from './numbers';
import type { Document } from './tree';

export const DEFAULT_SOURCE = 'api.json';

export async function loadDocument(source: string): Promise<Document> {
  const response = await fetch(source);
  if (!response.ok) throw new Error(`${source}: ${response.status} ${response.statusText}`);
  return validate(parse(await response.text()));
}

export async function readFile(file: File): Promise<Document> {
  return validate(parse(await file.text()));
}

/**
 * Enough of a check to give a useful message.
 *
 * Not a schema: the one mistake worth catching by name is pointing this at a
 * `pyraml graph --format json` output, which is also JSON, also has nodes, and
 * would otherwise render as an empty document with no explanation.
 */
function validate(value: unknown): Document {
  if (typeof value !== 'object' || value === null) throw new Error('not a JSON object');
  const document = value as Partial<Document> & { nodes?: unknown };
  if (document.nodes !== undefined && document.endpoints === undefined) {
    throw new Error('this looks like `pyraml graph --format json`; this app reads `pyraml tree` output');
  }
  for (const key of ['types', 'endpoints', 'annotation_types', 'security_schemes'] as const) {
    if (typeof document[key] !== 'object' || document[key] === null) {
      throw new Error(`missing "${key}": expected the output of \`pyraml tree FILE\``);
    }
  }
  return document as Document;
}
