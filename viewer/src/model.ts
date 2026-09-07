/**
 * Reading a `pyraml tree` document.
 *
 * The *types* are generated -- `tree.d.ts`, written by
 * `python -m pyraml.views.bindings` from the emitter itself. Nothing here
 * restates them. What is here is everything the JSON does not carry and a
 * reader needs: which addresses have a page, what to call one, and the path
 * nesting the model flattened.
 *
 * The metamodel is three constructs (docs/16 § 11.10):
 *
 *   {"$ref": <address>}                          a link -- look the target up
 *   {"type": "recursive", "head": {"$ref": ...}} repeats from here, do not expand
 *   anything else                                containment -- descend
 *
 * A consumer descends containment, follows a link when it chooses to, and stops
 * at a recursion marker. It keeps no ancestor set, which is why `<ShapeView>`
 * recurses without a depth budget.
 */

import type { Address, Document, Endpoint, Operation, Ref, Shape } from './tree';

export type {
  Address,
  Applied,
  DescribedBy,
  Document,
  DocumentAnnotation,
  Endpoint,
  EntryPoint,
  Json,
  Operation,
  Parameter,
  PatternProperty,
  Property,
  Recursion,
  Ref,
  Response,
  SecuredBy,
  SecurityScheme,
  Shape,
} from './tree';

/* -- telling the three constructs apart --------------------------------------- */

/** A link. The sole key is the test: an expanded shape carries `id` too. */
export function isRef(node: unknown): node is Ref {
  return typeof node === 'object' && node !== null && '$ref' in node && Object.keys(node).length === 1;
}

/**
 * A recursion marker. Distinct from a link on purpose: merging the two would
 * force every consumer to carry an ancestor set (docs/16 § 11.7).
 */
export function isRecursive(node: unknown): node is Shape & { head: Ref } {
  return typeof node === 'object' && node !== null && (node as Shape).type === 'recursive';
}

/* -- the index ---------------------------------------------------------------- */

export type Section = 'type' | 'annotationType' | 'securityScheme' | 'endpoint';

export interface Entry {
  address: Address;
  name: string;
  section: Section;
  /** The file the declaration was written in; absent for an endpoint. */
  file?: string;
  /** The route that shows it. Every `$ref` becomes a link through this. */
  href: string;
}

/**
 * Every addressable thing that has a page, by address.
 *
 * Declarations only. A nested anonymous shape has an address too and no page of
 * its own -- it is rendered where it sits. A `$ref` only ever names a
 * declaration, which is the rule the emitter applies in `reference`, so a
 * lookup that misses here is a genuinely dangling reference and is shown as one
 * rather than papered over.
 */
export class Index {
  readonly byAddress = new Map<Address, Entry>();
  /** The declared shapes, so a link can also be expanded where it sits. */
  readonly shapes = new Map<Address, Shape>();

  constructor(document: Document) {
    for (const { file, name, value } of declarations(document.types)) {
      this.add(value.id, name, 'type', file);
      if (value.id !== null) this.shapes.set(value.id, value);
    }
    for (const { file, name, value } of declarations(document.annotation_types)) {
      this.add(value.id, name, 'annotationType', file);
      if (value.id !== null) this.shapes.set(value.id, value);
    }
    for (const { file, name, value } of declarations(document.security_schemes)) {
      this.add(value.id, name, 'securityScheme', file);
    }
    for (const [path, endpoint] of Object.entries(document.endpoints)) {
      this.add(endpoint.id, path, 'endpoint');
      // An operation's address resolves to its resource's page: that is where
      // it is rendered, and a `$ref` at one should land somewhere it is visible.
      for (const operation of Object.values(endpoint.operations)) {
        this.add(operation.id, path, 'endpoint');
      }
    }
  }

  private add(address: Address | null, name: string, section: Section, file?: string): void {
    if (address === null || this.byAddress.has(address)) return;
    this.byAddress.set(address, { address, name, section, file, href: hrefOf(section, name, file) });
  }

  get(address: Address | null | undefined): Entry | undefined {
    return address == null ? undefined : this.byAddress.get(address);
  }

  shape(address: Address | null | undefined): Shape | undefined {
    return address == null ? undefined : this.shapes.get(address);
  }

  /** What to call a reference, falling back to the address's last segment. */
  label(address: Address | null | undefined): string {
    const entry = this.get(address);
    if (entry) return entry.name;
    if (address == null) return 'unresolved';
    return decodeURIComponent(address.split('/').pop() ?? address);
  }
}

export function hrefOf(section: Section, name: string, file?: string): string {
  const at = (kind: string) => `/${kind}/${encodeURIComponent(file ?? '')}/${encodeURIComponent(name)}`;
  switch (section) {
    case 'type':
      return at('types');
    case 'annotationType':
      return at('annotation-types');
    case 'securityScheme':
      return at('security');
    default:
      return `/endpoints/${encodeURIComponent(name)}`;
  }
}

/* -- reading a shape ----------------------------------------------------------- */

/** Keys that are structure rather than constraint, so a facet list skips them. */
const NOT_A_FACET: ReadonlySet<string> = new Set([
  'id',
  'name',
  'type',
  'type_expr',
  'display_name',
  'description',
  'required',
  'inherits',
  'annotations',
  'properties',
  'pattern_properties',
  'items',
  'any_of',
  'head',
  'example',
  'examples',
  'default',
  'enum',
  'xml',
  'custom_facets',
  'declares_facets',
  'allowed_targets',
]);

/**
 * The constraints a shape carries, in RAML spelling.
 *
 * The JSON uses the model's field names, which are `snake_case` (docs/16
 * § 11.9); RAML's are `lowerCamelCase` throughout, with no exceptions --
 * `pyraml/types/base.py` says so, and dropped an exception table for saying
 * nothing plain camel case did not. So this is a spelling change and not a
 * translation table that can go stale.
 */
export function facetsOf(shape: Shape): [string, unknown][] {
  return Object.entries(shape)
    .filter(([key, value]) => !NOT_A_FACET.has(key) && value !== null && value !== undefined)
    .map(([key, value]) => [camel(key), value] as [string, unknown]);
}

export function camel(name: string): string {
  return name.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

/** A shape's heading: its own name, else what it inherits, else its kind. */
export function titleOf(shape: Shape, index: Index): string {
  if (shape.name) return shape.name;
  const parent = shape.inherits?.[0];
  if (parent && isRef(parent)) return index.label(parent.$ref);
  return shape.type;
}

/** The type name a reader recognises: the expression as written, else the kind. */
export function spelling(shape: Shape): string {
  return typeof shape.type_expr === 'string' && shape.type_expr.trim() ? shape.type_expr.trim() : shape.type;
}

/* -- endpoints as a tree -------------------------------------------------------- */

export interface PathNode {
  /** The full path, which is also the key in `document.endpoints`. */
  path: string;
  /** What is new relative to the parent, for a navigation label. */
  segment: string;
  endpoint?: Endpoint;
  children: PathNode[];
}

/**
 * `document.endpoints` is flat and keyed by full path, because that is what the
 * model holds after P6 propagated URI parameters down. A reader navigates by
 * nesting, so it is rebuilt here -- from the keys alone, which is why a
 * resource that declares no method of its own still appears as a branch.
 */
export function pathTree(endpoints: Record<string, Endpoint>): PathNode[] {
  const roots: PathNode[] = [];
  const byPath = new Map<string, PathNode>();

  for (const path of Object.keys(endpoints).sort()) {
    let prefix = '';
    let parent: PathNode | undefined;
    for (const segment of path.split('/').filter(Boolean)) {
      prefix += `/${segment}`;
      let child = byPath.get(prefix);
      if (!child) {
        child = { path: prefix, segment: `/${segment}`, children: [] };
        byPath.set(prefix, child);
        (parent ? parent.children : roots).push(child);
      }
      parent = child;
    }
  }
  for (const [path, endpoint] of Object.entries(endpoints)) {
    const node = byPath.get(path);
    if (node) node.endpoint = endpoint;
  }
  return roots;
}

const METHOD_ORDER = ['get', 'head', 'post', 'put', 'patch', 'delete', 'options', 'trace'];

export function methodsOf(endpoint: Endpoint): [string, Operation][] {
  const rank = (method: string) => {
    const at = METHOD_ORDER.indexOf(method);
    return at === -1 ? METHOD_ORDER.length : at;
  };
  return Object.entries(endpoint.operations).sort(([a], [b]) => rank(a) - rank(b));
}

/** Every declaration of one section, flattened across the files it came from. */
export function declarations<T>(byFile: Record<string, Record<string, T>>): { file: string; name: string; value: T }[] {
  return Object.entries(byFile).flatMap(([file, declared]) =>
    Object.entries(declared).map(([name, value]) => ({ file, name, value })),
  );
}
