/**
 * Reading a `fastraml tree` document.
 *
 * The *types* are generated -- `tree.d.ts`, written by
 * `python -m fastraml.views.bindings` from the emitter itself. Nothing here
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

import { Tree, isRecursion, isRef } from './walk';
import type { Address, Endpoint, EntryPoint, HttpMethod, Json, Operation, Recursion, Ref, SecurityScheme, Shape, ShapeNode } from './tree';

export type {
  Address,
  Applied,
  DescribedBy,
  Document,
  DocumentAnnotation,
  Endpoint,
  EntryPoint,
  Example,
  Json,
  ObjectShape,
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
  ShapeNode,
} from './tree';

/* -- telling the three constructs apart --------------------------------------- */

/**
 * The metamodel comes from `walk.ts`, which is generated alongside `tree.d.ts`
 * and is the same reading every consumer of the contract gets. This app used to
 * spell both predicates itself; they are rules the *contract* states, so a copy
 * here was a second place for them to be right -- and the Go binding's copy was
 * a different predicate for two years without anything noticing (docs/16
 * § 11.11e). `isRecursive` keeps its name here, which is what this app has
 * always called it.
 */
export { Tree, UnreadableTree, isRef, isShape } from './walk';
export { isRecursion as isRecursive } from './walk';

/* -- the index ---------------------------------------------------------------- */

export type Section = 'type' | 'annotationType' | 'securityScheme' | 'endpoint' | 'operation';

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
  /** The declared schemes, so a `securedBy` entry can be read where it sits. */
  readonly schemes = new Map<Address, SecurityScheme>();
  /** The contract's reading of the document. Shape lookup delegates to it. */
  readonly tree: Tree;

  constructor(tree: Tree) {
    this.tree = tree;
    const document = tree.document;
    for (const { file, name, value } of declarations(document.types)) {
      if (isRef(value)) continue;
      this.add(value.id, name, 'type', file);
    }
    for (const { file, name, value } of declarations(document.annotation_types)) {
      if (isRef(value)) continue;
      this.add(value.id, name, 'annotationType', file);
    }
    for (const { file, name, value } of declarations(document.security_schemes)) {
      this.add(value.id, name, 'securityScheme', file);
      if (value.id !== null) this.schemes.set(value.id, value);
    }
    for (const [path, endpoint] of Object.entries(document.endpoints)) {
      this.add(endpoint.id, path, 'endpoint');
      // An operation has a page of its own. A resource with six methods is six
      // pages of detail on one screen otherwise, and only one of them is ever
      // the one being read.
      for (const [method, operation] of methodsOf(endpoint)) {
        this.add(operation.id, `${method} ${path}`, 'operation', undefined, `/endpoints/${encodeURIComponent(path)}/${method}`);
      }
    }
  }

  private add(address: Address | null, name: string, section: Section, file?: string, href?: string): void {
    if (address === null || this.byAddress.has(address)) return;
    this.byAddress.set(address, { address, name, section, file, href: href ?? hrefOf(section, name, file) });
  }

  get(address: Address | null | undefined): Entry | undefined {
    return address == null ? undefined : this.byAddress.get(address);
  }

  /** The page for a declaration, following a transparent alias to its target. */
  declaration(node: Shape | Ref): Entry | undefined {
    return this.get(isRef(node) ? node.$ref : node.id);
  }

  /**
   * The shape an address names, through `walk.ts`'s index.
   *
   * Not a map of declarations built here: `Tree` indexes every addressed shape
   * by the generated walk, so a position this app never enumerated still
   * resolves. The Python consumer's hand-written equivalent missed
   * `entry_point.base_uri_parameters` for exactly that reason.
   */
  shape(address: Address | null | undefined): Shape | undefined {
    return address == null ? undefined : this.tree.at(address);
  }

  /** What a shape is rather than how it arrived; the rule is the contract's. */
  content(shape: Shape): Shape {
    return this.tree.content(shape);
  }

  scheme(address: Address | null | undefined): SecurityScheme | undefined {
    return address == null ? undefined : this.schemes.get(address);
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
  'declared_facets',
  'allowed_targets',
  // Shown as a line of their own: a discriminator names a *property*, which a
  // chip reading `discriminator kind` beside `maxLength 200` does not say.
  'discriminator',
  'discriminator_value',
  // A JSON-schema type's two forms. Both are whole documents, neither is a
  // constraint, and `contentOf` is what reads the second.
  'json_schema',
  'projection',
]);

/**
 * The constraints a shape carries, in RAML spelling.
 *
 * The JSON uses the model's field names, which are `snake_case` (docs/16
 * § 11.9); RAML's are `lowerCamelCase` throughout, with no exceptions --
 * `fastraml/types/base.py` says so, and dropped an exception table for saying
 * nothing plain camel case did not. So this is a spelling change and not a
 * translation table that can go stale.
 */
export function facetsOf(shape: Shape): [string, Json][] {
  return Object.entries(shape)
    .filter(([key, value]) => !NOT_A_FACET.has(key) && value !== null && value !== undefined)
    .map(([key, value]) => [camel(key), value as Json]);
}

/**
 * A field name as a reader would say it: `accessTokenUri` -> `Access token URI`.
 *
 * The JSON carries the model field names (docs/16 section 11.9), which is right
 * for a wire format and wrong for a label over a value. Derived rather than
 * tabulated so a settings key this app has never seen still reads as English --
 * a security scheme's settings are open-ended, and a table would show the raw
 * name for anything not in it.
 */
export function humanise(name: string): string {
  const spaced = camel(name)
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim();
  const words = spaced.split(/\s+/).map((word) => (ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : word.toLowerCase()));
  const [first = '', ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ');
}

const ACRONYMS = new Set(['uri', 'url', 'urls', 'uris', 'id', 'api', 'http', 'https', 'ttl', 'jwt', 'oauth']);

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

/**
 * The type name a reader recognises: the expression as written, else the kind.
 *
 * `borrowed` says the expression on this shape belongs to its **container**, so
 * only its `type` is its own. That is the state of every union member and every
 * inlined supertype: P7 builds all of them from the one node the expression was
 * written on, so each carries the whole of it. `type: string | number` gives two
 * members that both read `string | number`, and `type: Search` at a query
 * parameter gives two members that *still* read `string | number` while the
 * parameter itself reads `Search` -- which is why comparing the two strings
 * does not find it, and why this is a flag and not a comparison.
 */
export function spelling(shape: Shape, borrowed = false): string {
  const written = typeof shape.type_expr === 'string' ? shape.type_expr.trim() : '';
  return borrowed || !written ? shape.type : written;
}

/**
 * Whether an array's own expression says no more than its `items` do, so the
 * items may be read in its place.
 *
 * `type: array` says nothing; `type: Review[]` says exactly what `items` says,
 * in text rather than as a link. Both defer. `type: Prices` on an array does
 * not -- it names a declaration, and computing `Money[]` from the items loses
 * the one word that identifies the type.
 */
export function namedByItems(shape: Shape, borrowed: boolean): boolean {
  const written = spelling(shape, borrowed);
  return written === 'array' || written.endsWith('[]');
}

/**
 * The same question for a union: does its expression say no more than its
 * members do?
 *
 * `type: string | number` does, and is better read as its members. `type:
 * Search` does not -- it is the name of a declaration whose *definition*
 * happens to be a union, and a query parameter typed `Search` that reads
 * `string | number` has had the one word identifying it replaced by its
 * contents.
 */
export function namedByMembers(shape: Shape, borrowed: boolean): boolean {
  const written = spelling(shape, borrowed);
  return written === 'union' || written.includes('|');
}

/**
 * How many members of a union are spelled out before the rest become a count.
 *
 * A union is a *type name* in every position this appears in -- a heading, an
 * attribute's head line -- and a name has to fit on the line it shares with
 * everything else. Nine members do not. The members are never only here: a
 * union always renders its `anyOf` selector below, which is where the whole
 * list lives.
 */
export const MEMBERS_SPELLED = 3;

/**
 * A shape's name with one spelling for arrays: `X[]`, always.
 *
 * `spelling` alone gives whatever the author wrote, and RAML lets them write an
 * array two ways -- `type: Review[]` and `type: array` with `items: string` --
 * so one page read `reviews Review[]` above `tags array of string`. Two
 * spellings of one idea, and which one appeared was an accident of the source.
 * The item is named from `items` rather than from the expression, so the two
 * forms converge and a nested array reads `string[][]` rather than `array[]`.
 *
 * A union is spelled from its members for the same reason, and truncated past
 * `MEMBERS_SPELLED`. `type_expr` is the author's text and can be any length: a
 * nine-member union is one unbreakable run in a row that has a line to fit in.
 *
 * Recursion terminates on containment: the emitter marks every cycle, and a
 * marker is named, not descended.
 */
export function spellingOf(shape: Shape, index: Index, borrowed = false): string {
  // A JSON-schema type is named by its projection, which is the RAML shape the
  // schema describes. Its own expression is the `!include` that named the
  // schema -- a file path, not a type name -- and `json` is the mechanism the
  // type arrived by, which is not what it is.
  if (shape.type === TYPE_JSON) return shape.projection ? spellingOf(shape.projection, index, true) : shape.type;
  if (shape.type === 'union' && shape.any_of && shape.any_of.length > 0 && namedByMembers(shape, borrowed)) {
    const members = shape.any_of;
    const names = members.map((member) => labelOf(member, index));
    const shown = names.slice(0, MEMBERS_SPELLED).join(' | ');
    return names.length > MEMBERS_SPELLED ? `${shown} | +${names.length - MEMBERS_SPELLED} more` : shown;
  }
  const written = spelling(shape, borrowed);
  if (shape.type !== 'array' || !namedByItems(shape, borrowed)) return written;
  const items = shape.items;
  if (items === null || items === undefined) return 'any[]';
  if (isRef(items)) return `${index.label(items.$ref)}[]`;
  if (isRecursion(items)) return `${items.name ?? 'recursive'}[]`;
  return `${spellingOf(items, index, true)}[]`;
}

/**
 * The kind whose content is a projection of a schema rather than RAML facets.
 *
 * A shape of this kind carries no RAML facet of its own -- the spec forbids one
 * beside a schema -- so everything it says is in `projection`, and every reader
 * of a shape's content follows that first.
 */
export const TYPE_JSON = 'json';

/** What a JSON-schema type says. `Index.content` is the same rule with a tree. */
export function contentOf(shape: Shape): Shape {
  return shape.type === TYPE_JSON && shape.projection ? shape.projection : shape;
}

/**
 * Whether a shape says anything its name does not.
 *
 * The question every collapsed row asks: is there something behind this name,
 * and is it worth a control? Not "does it have properties" -- that is true of
 * an object and false of every other kind, and a named `string` with a
 * `pattern` has as much to say as an object with two fields.
 *
 * Facets are counted through `facetsOf`, so a kind this app has never heard of
 * still answers yes: the deny-list is what is *not* a constraint, and anything
 * left over is one.
 */
export function detailed(shape: Shape | null | undefined): shape is Shape {
  if (shape === null || shape === undefined) return false;
  const structural =
    (shape.type === 'object' && Boolean(shape.properties || shape.pattern_properties || shape.discriminator)) ||
    (shape.type === 'union' && Boolean(shape.any_of)) ||
    (shape.type === 'array' && shape.items !== undefined) ||
    (shape.type === 'json' && shape.json_schema !== undefined);
  return Boolean(
    structural ||
      shape.enum ||
      shape.custom_facets ||
      shape.example !== undefined ||
      shape.examples ||
      shape.default !== undefined ||
      shape.xml !== undefined ||
      shape.declared_facets ||
      shape.allowed_targets ||
      facetsOf(shape).length > 0,
  );
}

/**
 * The same question about a node that may be any of the three constructs.
 *
 * A recursion marker answers no: it is a stop, and what it says -- that the
 * structure repeats, and from where -- fits on the line that names it. Opening
 * one is the loop.
 */
export function leadsSomewhere(node: Shape | Ref | Recursion | null | undefined, index: Index): boolean {
  if (node === null || node === undefined) return false;
  if (isRef(node)) return detailed(index.shape(node.$ref));
  if (isRecursion(node)) return false;
  return detailed(node);
}

/**
 * What to call one member of a union or one inlined supertype.
 *
 * The three constructs, in the order the metamodel puts them: a link is named
 * by its target, a recursion marker by what repeats, and anything else by its
 * own `type` -- never by `type_expr`, for the reason `spelling` gives.
 */
export function labelOf(member: Shape | Ref | Recursion, index: Index): string {
  if (isRef(member)) return index.label(member.$ref);
  if (isRecursion(member)) return member.name ?? 'recursive';
  return spellingOf(member, index, true);
}

/** A `facets:` entry, and the type that declared it. */
export interface FacetDeclaration {
  declared: ShapeNode;
  by: Shape;
  required: boolean;
}

/**
 * What declared the custom facet `name` that this shape supplies a value for.
 *
 * A `facets:` block declares what *subtypes* must supply, so the declaration is
 * never on the shape carrying the value -- it is on a supertype, on a page the
 * reader is not looking at. Without it a custom facet is a name and a string
 * with nothing saying what it was allowed to be.
 *
 * **Up `inherits[0]` only, which is what P10 does** (docs/10 section 4). The
 * validator's chain walk follows the first parent and no other, so a facet
 * declared on a second parent is one it does not see; finding it here would
 * show a reader a declaration that nothing checked the value against. The
 * limitation is tracked as a v1.1 item in that section, and both halves should
 * move together.
 */
export function facetDeclaration(shape: Shape, name: string, index: Index): FacetDeclaration | null {
  const seen = new Set<Shape>();
  let at: Shape | undefined = parent(shape, index);
  while (at !== undefined && !seen.has(at)) {
    seen.add(at);
    const found = at.declared_facets?.[name];
    if (found?.type != null) return { declared: found.type, by: at, required: found.required };
    at = parent(at, index);
  }
  return null;
}

/** The first supertype, as a shape, wherever it is one this document holds. */
function parent(shape: Shape, index: Index): Shape | undefined {
  const first = (shape.inherits ?? [])[0];
  if (first === undefined) return undefined;
  if (isRef(first)) return index.shape(first.$ref);
  return isRecursion(first) ? undefined : first;
}

/* -- where a request actually goes ----------------------------------------------- */

/**
 * The base URI with `{version}` filled in.
 *
 * `{version}` is the one base-URI parameter RAML resolves itself -- it is bound
 * to the API's own `version:`, not supplied by the caller -- so leaving it as a
 * placeholder shows a hole where there is a known value. Every other parameter
 * (`{tenant}`) stays written, because it *is* a hole, and the base URI
 * parameters table is what fills it.
 *
 * The trailing slash goes, so joining a path never doubles it.
 */
export function baseUriOf(api: EntryPoint | null | undefined): string {
  const written = api?.base_uri;
  if (!written) return '';
  const filled = api?.version ? written.split('{version}').join(api.version) : written;
  return filled.endsWith('/') ? filled.slice(0, -1) : filled;
}

/**
 * The scheme a caller uses, where the document narrows it.
 *
 * A method may declare `protocols:` narrower than the API's, and then the URL
 * for that method alone has a different scheme -- so it is part of the address,
 * not a footnote about it. Returns nothing when the base URI already agrees,
 * which is the ordinary case and would otherwise print the scheme twice.
 */
export function schemeOf(protocols: string[] | undefined, base: string): string | null {
  if (!protocols || protocols.length === 0) return null;
  const only = protocols.length === 1 ? protocols[0]!.toLowerCase() : null;
  if (only === null) return null;
  return base.startsWith(`${only}://`) ? null : only;
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

const METHODS: readonly HttpMethod[] = ['connect', 'delete', 'get', 'head', 'options', 'patch', 'post', 'put', 'trace'];

/** The `:method` URL segment is a bare string; this is what makes it a key of `operations`. */
export function isHttpMethod(value: string): value is HttpMethod {
  return (METHODS as readonly string[]).includes(value);
}

const METHOD_ORDER = ['get', 'head', 'post', 'put', 'patch', 'delete', 'options', 'trace'];
const METHOD_RANK = new Map(METHOD_ORDER.map((method, at) => [method, at]));

export function methodsOf(endpoint: Endpoint): [string, Operation][] {
  const rank = (method: string) => METHOD_RANK.get(method) ?? METHOD_ORDER.length;
  return Object.entries(endpoint.operations).sort(([a], [b]) => rank(a) - rank(b));
}

/** Every declaration of one section, flattened across the files it came from. */
export function declarations<T>(byFile: Record<string, Record<string, T>>): { file: string; name: string; value: T }[] {
  return Object.entries(byFile).flatMap(([file, declared]) =>
    Object.entries(declared).map(([name, value]) => ({ file, name, value })),
  );
}
