/**
 * Reading a `fastraml tree` document.
 *
 * Assembled by `python -m fastraml.views.bindings typescript --runtime FILE`. Do
 * not edit the assembled file. Edit
 * `fastraml/views/bindings/static/walk.ts`, which holds the reading; the
 * `CHILDREN` table below it is generated from `fastraml/views/tree.py` and the
 * kind classes in `fastraml/types/`, so a facet that starts holding a shape
 * starts being descended without either half being edited.
 * `tests/unit/test_bindings.py` fails when they disagree.
 *
 * The metamodel is three constructs (docs/16-graph.md section 11.10):
 *
 *   {"$ref": <address>}                            a link -- look the target up
 *   {"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
 *   anything else                                  containment -- descend
 *
 * A consumer descends containment, follows a link when it chooses to, and stops
 * at a recursion marker. It maintains no ancestor set.
 *
 * This holds only what the contract states. Naming, page routing and path
 * nesting belong to the consumer.
 *
 * The table lives here rather than in `tree.d.ts` for one reason: a declaration
 * file cannot hold a value. Python and Go keep theirs beside the types.
 */

import type { Address, Document, Recursion, Ref, Shape, ShapeNode } from './tree';

export class UnreadableTree extends Error {}

/** A link. Its sole key is the test: an expanded shape carries `id` too. */
export function isRef(node: unknown): node is Ref {
  return typeof node === 'object' && node !== null && '$ref' in node && Object.keys(node).length === 1;
}

/**
 * A recursion marker. Distinct from a link on purpose: merging the two would
 * force every consumer to carry an ancestor set.
 */
export function isRecursion(node: unknown): node is Recursion {
  return typeof node === 'object' && node !== null && (node as Recursion).type === RECURSION_TYPE;
}

/** An expanded type: neither a link nor a marker, and it names its kind. */
export function isShape(node: unknown): node is Shape {
  return typeof node === 'object' && node !== null && (node as Shape).type in KINDS;
}

/** One `fastraml tree` document, with its addresses indexed. */
export class Tree {
  readonly document: Document;
  private readonly index = new Map<Address, Shape>();

  private constructor(document: Document) {
    this.document = document;
    for (const shape of this.shapes()) {
      if (shape.id !== null && shape.id !== undefined && !this.index.has(shape.id)) {
        this.index.set(shape.id, shape);
      }
    }
  }

  /**
   * Check the envelope, then index the document.
   *
   * A tree from a later format version may have renamed a field a consumer
   * reads. Reading it anyway produces output that is wrong rather than absent,
   * so refuse anything the three envelope fields do not match (docs/16 section
   * 11.9). A later format version may have renamed a field.
   */
  static of(document: unknown): Tree {
    return new Tree(Tree.check(document));
  }

  /**
   * The envelope alone, for a caller that wants to refuse a document before
   * doing anything with it. `of` indexes as well, which is the whole walk; a
   * loader that only needs to know whether it can read the file should not pay
   * for that and then discard it.
   */
  static check(document: unknown): Document {
    if (typeof document !== 'object' || document === null) {
      throw new UnreadableTree(`not a tree document: ${typeof document}`);
    }
    const found = document as Partial<Document>;
    if (found.format !== FORMAT || found.format_version !== FORMAT_VERSION || found.view !== VIEW) {
      throw new UnreadableTree(
        `expected ${FORMAT} v${FORMAT_VERSION} (${VIEW}), got ${String(found.format)} ` +
          `v${String(found.format_version)} (${String(found.view)}) -- regenerate with a matching fastraml`,
      );
    }
    return found as Document;
  }

  /* -- the three constructs ---------------------------------------------------- */

  /**
   * Follow a link, once.
   *
   * A recursion marker comes back as itself. Treating it as a link would break
   * the traversal law: a walker that expands links cannot tell a repeat from a
   * fresh subtree.
   */
  resolve(node: ShapeNode | null | undefined): Shape | Recursion | undefined {
    if (node === null || node === undefined) return undefined;
    if (isRecursion(node)) return node;
    if (isRef(node)) return this.index.get(node.$ref);
    return isShape(node) ? node : undefined;
  }

  /** The shape an address names, or nothing. */
  at(address: Address): Shape | undefined {
    return this.index.get(address);
  }

  /**
   * What a shape is, rather than how it arrived.
   *
   * A `json` shape carries its schema twice: `json_schema` in JSON Schema's own
   * vocabulary, and `projection` as the nearest RAML shape. The projection is
   * the type (docs/16 section 11.10). The `json` shape itself has no properties
   * and no facets, because the spec forbids a JSON-schema type from taking part
   * in inheritance.
   */
  content(shape: Shape): Shape {
    return shape.type === 'json' && shape.projection ? shape.projection : shape;
  }

  /* -- containment -------------------------------------------------------------- */

  /**
   * The shape nodes directly under a node.
   *
   * A link and a recursion marker have none: both are stops. Neither is
   * followed here, so the walk needs no visited set.
   */
  *children(node: ShapeNode | null | undefined): Generator<ShapeNode> {
    if (!isShape(node)) return;
    yield* under(node, 'ShapeBase');
    yield* under(node, KINDS[node.type]!);
  }

  /**
   * Every expanded shape in the document, by containment.
   *
   * No ancestor set and no visited set. Containment is a tree, and the two
   * constructs that would make it a graph are both stopped at.
   */
  *shapes(): Generator<Shape> {
    for (const node of under(this.document, 'Document')) yield* this.expand(node);
  }

  private *expand(node: ShapeNode | null | undefined): Generator<Shape> {
    if (!isShape(node)) return;
    yield node;
    for (const child of this.children(node)) yield* this.expand(child);
  }
}

/**
 * Every shape node one record holds, however deeply its own records nest.
 *
 * Generic over `CHILDREN`, so `Endpoint` -> `Operation` -> `Response` ->
 * `bodies` is the table's business and not this function's.
 */
function* under(value: unknown, record: string): Generator<ShapeNode> {
  if (typeof value !== 'object' || value === null) return;
  for (const [key, container, holds, of] of CHILDREN[record] ?? []) {
    for (const item of each((value as Record<string, unknown>)[key], container)) {
      if (holds === 'record') yield* under(item, of);
      else if (item !== null && item !== undefined) yield item as ShapeNode;
    }
  }
}

function* each(value: unknown, container: string): Generator<unknown> {
  if (value === null || value === undefined) return;
  if (container === 'one') yield value;
  else if (container === 'list') yield* value as unknown[];
  else if (container === 'map') yield* Object.values(value as Record<string, unknown>);
  else if (container === 'map_of_map') {
    for (const inner of Object.values(value as Record<string, Record<string, unknown>>)) {
      yield* Object.values(inner);
    }
  }
}
