# 16. Views and tree contract

**Status: built.** `fastraml.views` contains read-only projections of a parsed,
effective RAML model. The CLI exposes them through `graph`, `tree`, `show`,
`list`, `refs`, `deps`, `query`, `compat`, `convert`, and `serve`.

This document owns the view-layer boundary and durable contracts shared by view
consumers. It does not restate RAML rules, implementation history, or generated
tree declarations.

## 1. View-layer boundary

Views run after parsing and decide no RAML rule. A view may read the model but
must not mutate it. Nothing under `fastraml/parser/` or `fastraml/types/` may
import `fastraml.views`; outside that package only the composition roots
`fastraml/cli.py` and `fastraml/service/` may import a view.
`tests/unit/test_views.py` enforces both directions.

Use an unwrapped model for every effective view:

```python
from fastraml import ParseOptions, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True))
```

The tree and graph are separate projections of that model:

- The graph represents identity and references as addressed nodes and edges.
- The tree represents containment and preserves data that has no graph edge.
- Neither output can be reconstructed losslessly from the other.

`fastraml/views/walk.py` assigns structural addresses for both. An address in a
tree reference names the corresponding graph node when both projections use the
same address map.

## 2. Shared addresses

Addresses are structural IRIs rooted at `fastraml://id`, for example:

```
fastraml://id#/declarations/types/User
fastraml://id/lib.raml#/declarations/types/Address
fastraml://id#/web-api/endpoint/%2Fusers/supportedOperation/get/returns/200
```

`Walk` registers declarations before use sites and assigns an entity's first
address once. Segments are percent-escaped. The workspace root supplies the
relative unit path, so addresses do not expose an absolute filesystem path.

A registered DataType fragment has one canonical declaration address under its
own file, reserved before any inclusion site is visited. Its short name is the
file basename, including the extension; its file qualifier is the path relative
to the workspace root for a local file, or the full resolved HTTP(S) URI for a
remote file. Query components in that URI are retained. For example,
`models/User.raml` declares `User.raml` at
`fastraml://id/models%2FUser.raml#/declarations/types/User.raml`.
Different files may use the same short name without sharing an identity.
Different include spellings that resolve to the same fragment share its address.

An address is an address, not a replacement for model identity. Address lookup
may be many-to-one where model entities are linked. Consumers must use the
address supplied by a projection rather than reconstructing one.

## 3. Graph projection

`fastraml.views.graph.build_graph(raml)` returns a `Graph` over the effective
model. `RAML_NS` is the graph vocabulary namespace and is exported from the
package root; its value remains provisional before 1.0.

Graph nodes hold references to model entities. Node attributes translate model
values into graph vocabulary; they are derived on access. Edges express semantic
relationships, including declaration ownership, endpoint containment, request
and response payloads, parameter bindings, type structure, template use,
security use, and annotation use.

The exported edge closures are part of the API:

- `TYPE_EDGES`: what a type is made of.
- `USE_EDGES`: type structure plus containment and applications.

`Graph.walk()` is breadth-first and returns `Route` values containing every node
and predicate on the selected route. `Graph.out()` and `Graph.into()` preserve
edge insertion order. `Graph.request_shape_iris()` returns shapes reachable from
request input sites.

`Graph.find()` accepts a name or full IRI. It prefers declarations over internal
nodes and preserves genuine declaration ambiguity. If an endpoint has a
`displayName`, its resource path remains accepted as a lookup name. `Graph.entries()`
provides the navigable inventory used by `fastraml list`; `Graph.suggest()` only
suggests names and never resolves a miss implicitly.

The graph CLI formats are Turtle, N-Triples, DOT, and JSON:

```bash
fastraml graph api.raml
fastraml graph --format json -o graph.json api.raml
fastraml refs api.raml User
fastraml deps api.raml User
```

`refs` follows use edges in reverse. `deps` follows type structure for types and
use containment for other node kinds. Both commands can filter by kind, depth,
and result limit. `refs --sites` prints where the name is written instead,
from the occurrence index (§ 9).

### 3.1 SPARQL catalogue

`fastraml query` runs SPARQL over the graph and requires the optional
`pyoxigraph` dependency. Listing and showing catalogue queries need neither a
document nor the optional dependency:

```bash
fastraml query --list
fastraml query --show endpoint-tree
fastraml query api.raml -n endpoint-tree
fastraml query api.raml -q 'ASK { ?s ?p ?o }'
```

The current catalogue is defined by `fastraml/views/queries.py`; do not copy its
volatile inventory here. Use `refs` and `deps` for parameterized navigation when
the route matters.

## 4. Effective reading view

`fastraml show FILE NAME` finds a graph entity and renders its effective model
form. It supports types, endpoints, and operations. Rendering reads the model,
not graph attributes, because the graph intentionally omits detailed shape data.

```bash
fastraml show api.raml User
fastraml show --depth 2 api.raml /users
```

The output is RAML-shaped YAML with source notes where model provenance is
available. It shows effective inheritance, properties, constraints, annotations,
custom facets, security descriptions, and structured JSON Schema projections.
`--depth` controls structural expansion; recursion remains finite.

This is a human-readable display, not a RAML export. Explanatory fields such as
`inherits` and expanded security descriptions are part of the display, and prose
is limited to its first line. The YAML preserves enum value types but is not
guaranteed to parse as a RAML declaration. Use the format exports in § 8 when
you need a document for another tool.

A member a trait or resource type contributed is noted with its name: the
declaration whose span, key through value and columns included, holds the
member's key, and only when the site applied it. A line alone would not tell
two declarations written on one line apart.

Traits, resource types, security schemes, and other declaration kinds without an
effective standalone form are reported as such. Use `refs` to locate their
effective application sites.

## 5. Compatibility view

`fastraml compat OLD NEW` compares two effective API models and reports changes
with an impact: `breaking`, `review`, `compatible`, or `cosmetic`. It exits 1
when any breaking change exists.

```bash
fastraml compat old.raml new.raml
fastraml compat --types old-library.raml new-library.raml
fastraml compat --json -o changes.jsonl old.raml new.raml
```

The default mode compares effective operations. `--types` compares entry-point
`types:` declarations and reports their request and response implications. The
JSON records are the machine contract; Markdown is the reading view. Compatibility
policy is configurable through the common configuration and repeatable `--rule`
overrides. See `fastraml/views/backward/` and `tests/unit/test_cli.py` for the
supported record and CLI behavior.

Protocols are compared as effective sets (docs/08 § 6.1). Undetermined
protocols, with no `protocols:` and no literal `http`/`https` baseUri scheme,
are compared with nothing: they are neither removed nor added. Without
`protocols:`, an edit of the baseUri scheme from `http` to `https` reports two
changes, `base-uri-changed` for the address and `protocol-removed` for the
transport; they are different facts and each has its own rule override.

## 6. Effective document tree

`fastraml.views.tree.build_tree(raml)` produces the addressed effective tree.
The CLI emits it as JSON:

```bash
fastraml tree api.raml
fastraml tree --positions api.raml
```

The stable envelope is:

```json
{
  "format": "fastraml-tree",
  "format_version": 1,
  "view": "effective"
}
```

The tree preserves declaration order. Positions are intentionally separate:
`positions_of(raml)` and `fastraml tree --positions` produce the position view.

### 6.1 Traversal contract

Consumers use exactly three forms:

| Form | Meaning | Consumer action |
|---|---|---|
| `{"$ref": ADDRESS}` | link | resolve the address |
| `{"type": "recursive", "head": {"$ref": ADDRESS}}` | recursion marker | stop expansion |
| any other object | containment | descend |

A consumer descends containment, follows links, and stops at recursion markers.
It does not need an ancestor set or an independent RAML resolver.

Named declarations are referenced instead of duplicated. Anonymous structural
content is inlined where it would otherwise have no representation. An anonymous
alias, such as the items of `Price[]`, is a transparent reference to its
referent. A declared alias (`ID: Key`) is a declaration like any other: it is
listed under its own name and address with the effective facets it shares with
its referent (docs/07 § 3), and every reference to `ID` links to it. Its
`alias` key links the referent, one step of a chain at a time; only a declared
alias carries the key. A `types` or `annotation_types` entry is therefore always
a shape, never a bare link. Annotation applications include their bound type and
value at the application site.

The `types` inventory includes every registered DataType fragment root, even
when only a body or a property includes it. Each root is listed once under its
file qualifier and short name (§ 2); inclusion sites reference it through
`inherits` and retain their own effective constraints. An
AnnotationTypeDeclaration root is listed in `annotation_types` and addressed
under `declarations/annotations`. External JSON Schemas registered as DataType
fragments follow the same rule. A headerless YAML include stays inline: it is
literal content whose meaning depends on the includer's namespace (docs/04 § 4.1).

### 6.2 Tree wire rules

- Tree keys use the model's snake_case field names.
- Required and optional keys, record shapes, closed vocabularies, and
  shape-bearing fields are defined by the generated bindings, not this document.
- Numeric value bounds (`minimum`, `maximum`, `multiple_of`) are exact decimal
  strings. Counts such as `min_length` and `max_items` are JSON numbers.
- JSON Schema shapes retain their source schema and a RAML-shape projection.
  Consumers read the projection when they need uniform shape structure. A
  schema with no projection (docs/10 § 7) has `json_schema` and no
  `projection`: an opaque leaf, while the rest of the tree is unaffected.
- Tree format changes are versioned by `format_version`. Additive optional keys
  do not require a version change; incompatible renames or reinterpretations do.

## 7. Generated bindings

`fastraml/views/bindings/` generates tree-contract bindings for TypeScript,
Python, and Go. Each backend can emit:

- types (`-o`)
- a runtime walker (`--runtime`)
- a conformance driver (`--conform`)

```bash
python -m fastraml.views.bindings typescript \
  -o viewer/src/tree.d.ts --runtime viewer/src/walk.ts
python -m fastraml.views.bindings python \
  -o contrib/raml-codegen/raml_codegen/tree.py \
  --runtime contrib/raml-codegen/raml_codegen/walk.py
```

The Go backend writes wherever its caller requests. Do not edit generated files.
Edit `fastraml/views/bindings/static/` only for target-language code that does
not vary with the tree contract.

`bindings/schema.py` is the single declaration of tree key sets and structural
kinds. It also decides which shape record declares each key (`shape_layout()`:
the discriminator on each variant, `json_schema` and `projection` on
`JsonShape` only), the envelope constants, and the recursion marker's keys; a
backend iterates these and only spells them. Generation fails when the emitter
writes an undeclared key.
`tests/unit/test_bindings.py` checks checked-in TypeScript and Python artifacts;
`tests/unit/test_conformance.py` checks the shared cross-language corpus. CI's
`bindings` job installs Go and Node and fails if those checks skip.

## 8. Other exports

`fastraml.views.raml.to_raml(json_shape, name=None)` returns a complete RAML
document from a compiled `JsonShape`. A schema without named definitions or
nested external targets becomes a DataType; a root-only external reference can
also be inlined unless recursive. With definitions (including unused ones),
recursive root references, or named targets needed by other types, it becomes
a Library containing the named types and
the root type, named for the schema file stem by default. References become
type names; recursive uses refer back to their named head. An inline schema
needs an explicit `name` when the result is a Library. JSON Pointer keys and
file stems that cannot be written as RAML type expressions are given safe names;
collisions (including with built-in types) receive numeric suffixes. Optional
properties write `required: false`; a literal name ending in `?` writes
`required:` explicitly for either value so the question mark stays in the name.
A literal `/regex/`-looking property cannot be distinguished from RAML's
pattern-property syntax, so export rejects it. RAML also rejects pattern
properties together with `additionalProperties: false`; export refuses that
combination rather than emitting an invalid document. A constrained anonymous
union member, an anonymous recursion, or a root name colliding with a definition
fails rather than silently losing a constraint. Fraction facets are written as
exact YAML numbers. The export uses the nearest-RAML shape
projection (docs/10 § 7), with its documented semantic losses; it is not a
lossless translation of JSON Schema. It is the one view that raises a schema's
projection error (`JsonShape.projection_error()`): the projection is its whole
output, so it has no opaque form to fall back to. `fastraml convert raml FILE.json [-o FILE]`
parses the schema through the normal loader and writes this document.

`fastraml.views.jsonschema.to_json_schema(shape)` returns a JSON Schema draft-07
document and any information the export could not represent. The input shape
must be unwrapped. A JSON-backed entry exports its bundled schema at the root
so local pointers still resolve. `fastraml convert jsonschema FILE.raml [TYPE]`
exports a DataType fragment directly; an API or Library requires the name of
one declared type. It writes JSON (`-o FILE` saves it) and reports dropped
information on stderr.

For RAML object types, explicit properties take precedence over patterns. The
export excludes explicit names and earlier matching patterns from each pattern's
domain, preserving RAML's first-match behavior rather than JSON Schema's usual
overlapping constraints. An extra key that matches no pattern is governed by
`additionalProperties` alone, as in RAML (docs/05 § 4). The patterns are
chained in the type's effective order, inherited ones first (docs/07 § 4).
Explicit names are escaped only where ECMA-262 syntax requires it, because
unicode-mode validators reject identity escapes such as `\-`. Each exclusion
is a prefix, so a global inline flag anywhere prevents both kinds. A capture
group in any pattern but the last prevents only the ordering between patterns,
because embedding that pattern would renumber the groups after it; explicit
names still win. Each precedence the export cannot keep is reported as a
conversion notice.

`fastraml.views.openapi.to_openapi(raml)` returns an OpenAPI 3.0.3 document and
loss notices. `fastraml convert openapi` emits YAML by default or JSON with
`--format json`; notices go to stderr. A JSON Schema type with no projection is
written without a type constraint, with a notice naming the projection error;
its schema is not embedded, since OpenAPI 3.0's schema object is not JSON
Schema and lacks several constructs that fail projection, such as conditionals
and tuple items. The RAML type including a schema file and a `$ref` reaching it
share one component when they are one projected shape; a file naming no draft
read in two drafts (docs/10 § 7) is two readings, and two components.
Named use sites retain a reference when their effective constraints and members
match the component. The match is read from the model, recursively through
members and their metadata, before any component is built: a narrowing leaves
no unreferenced component, and a matching use site reports no loss for a body
it never writes. A slot the comparison does not recognise counts as a
difference, so an unknown facet costs a reference, never a constraint. A use site that narrows a structural constraint or changes
members exports its full effective schema inline, preserving inherited
restrictions without composing it with a parent whose closed property set or
patterns could reject the new members. Metadata-only changes decorate the
reference through OpenAPI 3.0's `allOf` form. Enum-only narrowing also uses
`allOf` to intersect the use-site enum with the component's restrictions.
Reference refinements preserve value types, including booleans and numbers
inside structured defaults. Component keys keep only `[a-zA-Z0-9._-]`, the set
OpenAPI allows; other characters become `_`, so an inline body that heads a
cycle from property `next?` is named `next_`.

`fastraml.bound_base_uri(api)` is `baseUri` with `{version}` bound to the
root `version:`, the one base URI variable RAML binds itself; every other
variable stays written for the caller to supply. `version` declared under
`baseUriParameters` is the caller's too, as the OpenAPI export reads it.
`APIFragment.base_uri` stays as written.

Both schema exports write `datetime` as `format: date-time` and `date-only` as
`format: date`. The other date and time kinds have no matching format and are
written as a `pattern`: `time-only`, because JSON Schema's `time` requires an
offset and OpenAPI 3.0 defines none; `datetime-only`; and `datetime` with
`format: rfc2616`. The patterns are `TIME_ONLY_PATTERN`,
`DATETIME_ONLY_PATTERN` and `RFC2616_PATTERN` in `types/values.py`, and the
parser's validators compile the same grammar, so the export accepts exactly what
the parser accepts. The grammar spells out ASCII digits, day ranges, leap years,
fractional seconds and the leap second `:60`. The patterns use only syntax
ECMA-262 and Python `re` read alike, and end with `(?![\s\S])` rather than `$`,
which in Python also matches before a final newline; `test_jsonschema_view.py`
runs one corpus through both.

These are read-only views. Their detailed tests are
`tests/unit/test_jsonschema_view.py`, `tests/unit/test_openapi_view.py` and
`tests/unit/test_base_uri_view.py`.

### 8.1 Value samples

`fastraml.sample(shape, options=SampleOptions(), key='')` returns a
deterministic value the unwrapped shape accepts, or raises `SampleError`. A
mock server answers with it; documentation shows it. It is chosen in this order:

1. **Declared.** The shape's own examples that validate, skipping any marked
   `strict: false`; otherwise its own `default`; otherwise an `enum` member.
   `declared_values(shape)` returns the first two.
2. **Composed.** An object from its properties' values, an array from its
   items' values, a union from the first member that yields one. Each part
   follows these same rules, so a property's own example is used.
3. **Synthesized.** A scalar built to satisfy the shape's facets. Pattern
   support is best effort: the pattern's literal prefix and a fixed list of
   candidates are tried, and a pattern none of them matches is a
   `SampleError`.

A supertype's example is never tried. A subtype may narrow a facet or add a
required property, so nothing guarantees that its parent's example fits, and
examples are not inherited ([07](07-resolution-and-inheritance.md) § 4). A body
written `application/json: Book` is an alias of `Book` and carries its
examples ([07](07-resolution-and-inheritance.md) § 3). One written
`type: Book` is a subtype, so its value is composed from `Book`'s properties.

`SampleOptions(synthesize=False)` skips step 3. The value is then built from
declared data alone: an optional property is included only if its value uses
declared data, and a value with no declared data anywhere in it is a
`SampleError`. `seed` and `key` choose deterministically among declared
examples and synthesized variants. `collection_size` sets the array length to
aim for, and `optional_probability` sets the chance that an optional property
is included when synthesizing.

`named_example(shape, name)` returns the shape's own `examples:` entry of that
name, even if it is marked `strict: false`, provided it validates.

Every returned value has been validated against the shape, and none shares a
container with the model. Tests: `tests/unit/test_samples.py`.

## 9. Occurrence index

`fastraml.views.occurrences.build_occurrences(raml)` lists where each name is
written and which entity it names. It is what definition, references and
rename read. The model must be parsed with `retain_text=True`, which keeps
each file's text and none of its YAML tree; `retain_source=True` implies it.

An `Occurrence` holds five things:

- the file the name is written in;
- the span of the name token alone, so `lib.User` gives two occurrences;
- a role: `definition`, `reference`, `alias_prefix`, `builtin` or `link`;
- a kind;
- the target entity's `id`, and the text it expects at the span.

The index reads what the passes bound and resolves no name itself:

- **Definitions.** The five declaration tables of every API and library,
  `uses:` keys, object properties and `facets:` entries.
- **References.** The names in type expressions, which P7 records
  ([06](06-type-expressions.md) § 3). A built-in written alone, such as
  `type: string`, never reaches P7; it is read from the `type:` node the
  shape keeps, or, where a caller's value supplied it, recorded where the
  caller wrote it. The `type:` and `is:` entries of every
  resource and method. `securedBy:` names, and the name in each
  `(annotation)` key.
- **Links.** Each `!include` argument and each `uses:` value, as a `Link`:
  an occurrence that also records the URI its path resolved to, found or
  not, without a `#fragment`. The target is the fragment the file decoded
  to, or `None` for a file that is not one, such as a schema or a text file;
  the resolved URI names it either way.

A name in a quoted scalar is placed past its quote, as diagnostics are
(docs/11 § 3). A lenient model gives the occurrences of the stages it
completed. A span met
more than once, for example in a template applied twice, is kept once per
target.

**The law.** A candidate is kept only if the retained text at its span equals
the name it records. For a reference, that is the name its target is declared
under, so a wrong position and a wrong binding both fail. A rejected candidate
is kept in `Occurrences.dropped`. A name a template substituted is placed
where the caller wrote it (docs/08 § 5.1). One known cause remains: a
transformed value, `<<item | !pluralize>>`, which is written nowhere.

The law checks only the candidates the index finds. A name nothing records is
not a candidate, and nothing checks for one.

`Occurrences.at(uri, line, column)` finds the occurrences under a cursor.
`Occurrences.of(id)` lists an entity's definition and every use of it.

## 10. Authorship view

`fastraml.views.authored` answers what a file, or an entity, wrote, for every
consumer that lists a document as its author wrote it: the outline
(`docs/21` § 4), and later completion, rename and semantic tokens. It reads
facts the model records and selects by `location` over the merged model, so a
file an Extension or an Overlay adds to lists what it added there: a type in
the master's `types`, a method on a master resource.

- `declarations(raml, uri)`: every declaration located in `uri`, through
  `every_declaration` (`docs/04` § 1).
- `metadata`, `base_uri_parameters`, `documentation`, `uses` (each
  `(raml, uri)`): the API's `title`, `version` and `baseUri`, its base URI
  parameters and documentation items located in `uri` (a DocumentationItem
  file's own item among them), and `uri`'s own `uses:`.
- `fragment_body(raml, uri)`: what a DataType, AnnotationTypeDeclaration or
  SecurityScheme file is, its shape or definition.
- `properties(base)`, `pattern_properties(base)`, `facets(base)`: the members
  a type declares, not those it inherits. An inherited member keeps its
  declaration's shape, which a parent holds too; an alias declares none, since
  it shares its referent's containers (docs/07 § 3).
- `items(base)`: the items an array wrote, by `items_written` (docs/06 § 3).
- `bodies(owner, written)`: each body `owner` wrote, the bodies of one `body:`
  without a media type together, by `media_type_written` (docs/08 § 6.3).
- `resources(raml, uri)`: the resources `uri` wrote, or added methods or
  resources to, as it nests them; `here` says whether the resource's key is
  written in `uri`.
- `members(owner, found)`, `parameters(owner, written)`, `secured_by(owner)`,
  `wrote(parent, location, key)`: which responses, `is:` entries, parameters
  and schemes a resource, method, response or security scheme wrote.

Which template contributed a member is not recorded: `wrote` tells it from
the spans, and that is exact. A trait or resource type is declared in its
table or its own fragment, never inside a resource, a method or a type, so a
member it contributed lies outside its parent's span even in the parent's
file, and a member inside was written there. The placement law makes spans
hold what they were written with ([11](11-diagnostics.md) § 3.2). The same test tells a property a template merged into a declaration, and
an inherited one recursion marking gave a shape of its own, from the
declaration's own.

Every reader here goes through that one test. It compares spans as numbers,
not through `Position.spanning` and `contains`: it runs for every member of
every type, and building a `Position` for each cost the outline a third.

## 11. Verification

- View boundary: `tests/unit/test_views.py`
- Graph and tree behavior: `tests/unit/test_graph.py`, `tests/unit/test_cli.py`
- Occurrence index: `tests/unit/test_occurrences.py`; the law over the corpora, `tests/unit/test_occurrence_law.py`
- Tree bindings: `tests/unit/test_bindings.py`, `tests/unit/test_conformance.py`
- Consumer traversal law: `tests/unit/test_consumer_traversal.py`

For durable design rationale that is not part of the current contract, see
`docs/archive/views-consumers-history.md`.
