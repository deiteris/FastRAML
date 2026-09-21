/**
 * Answer the conformance corpus through this language's reading of the tree.
 *
 * Copied verbatim by `python -m fastraml.views.bindings typescript --conform
 * FILE`. Do not edit the copy. Edit
 * `fastraml/views/bindings/static/conform.ts`.
 *
 *   node --experimental-strip-types conform.ts <corpus-directory>
 *
 * Reads `cases.json`, runs `walk.ts` over each tree it names, and writes the
 * answers to stdout as JSON. It holds **no expectations**: the corpus has
 * those, and `tests/unit/test_conformance.py` does the comparing. A driver that
 * decided whether it had passed would be a driver that could decide it had.
 *
 * `conform.py` and `conform.go` are the same file. Each answer is a string or
 * a list of strings.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { Recursion, Ref, Shape, ShapeNode } from './tree';
import { Tree, UnreadableTree } from './walk';

function label(node: ShapeNode | null | undefined): string {
  if (typeof node !== 'object' || node === null) return 'none';
  if ('$ref' in node && Object.keys(node).length === 1) return `ref:${(node as Ref).$ref}`;
  if ((node as Recursion).type === 'recursive') return `recursive:${(node as Recursion).id ?? ''}`;
  return `shape:${(node as Shape).id ?? ''}`;
}

function addressOf(found: Shape | Recursion | undefined): string | null {
  return found?.id ?? null;
}

function answers(document: unknown, probes: string[]): unknown {
  const tree = Tree.of(document);
  const children: Record<string, string[]> = {};
  const resolve: Record<string, string | null> = {};
  const content: Record<string, string | null> = {};
  for (const address of probes) {
    children[address] = [...tree.children(tree.at(address))].map(label);
    resolve[address] = addressOf(tree.resolve({ $ref: address }));
    const found = tree.at(address);
    if (found !== undefined) content[address] = addressOf(tree.content(found));
  }
  return { shapes: [...tree.shapes()].map((shape) => shape.id ?? null), probes, children, resolve, content };
}

function refuses(envelope: unknown): boolean {
  try {
    Tree.of(envelope);
  } catch (error) {
    return error instanceof UnreadableTree;
  }
  return false;
}

function main(argv: string[]): void {
  const corpus = argv[2]!;
  const cases = JSON.parse(readFileSync(join(corpus, 'cases.json'), 'utf8')) as {
    cases: Record<string, { tree: string; expected: { probes: string[] } }>;
    refuse: unknown[];
  };
  const out: { cases: Record<string, unknown>; refuse: boolean[] } = {
    cases: {},
    refuse: cases.refuse.map(refuses),
  };
  for (const [name, one] of Object.entries(cases.cases)) {
    const document = JSON.parse(readFileSync(join(corpus, one.tree), 'utf8')) as unknown;
    out.cases[name] = answers(document, one.expected.probes);
  }
  process.stdout.write(JSON.stringify(out));
}

main(process.argv);
