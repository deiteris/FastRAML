# 16. Views and tree contract

**Status: built.** `fastraml.views` contains read-only projections of a parsed,
effective RAML model. The CLI exposes them through `graph`, `tree`, `show`,
`list`, `refs`, `deps`, `query`, `compat`, `openapi`, and `serve`.

This document owns the view-layer boundary and durable contracts shared by view
consumers. It does not restate RAML rules, implementation history, or generated
tree declarations.

## 1. View-layer boundary

Views run after parsing and decide no RAML rule. A view may read the model but
must not mutate it. Nothing under `fastraml/parser/` or `fastraml/types/` may
import `fastraml.views`; outside that package only `fastraml/cli.py` may import a
view. `tests/unit/test_views.py` enforces both directions.

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
and result limit.

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
content is inlined where it would otherwise have no representation. Aliases are
transparent references to their referents. Annotation applications include their
bound type and value at the application site.

### 6.2 Tree wire rules

- Tree keys use the model's snake_case field names.
- Required and optional keys, record shapes, closed vocabularies, and
  shape-bearing fields are defined by the generated bindings, not this document.
- Numeric value bounds (`minimum`, `maximum`, `multiple_of`) are exact decimal
  strings. Counts such as `min_length` and `max_items` are JSON numbers.
- JSON Schema shapes retain their source schema and a RAML-shape projection.
  Consumers read the projection when they need uniform shape structure.
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
kinds. Generation fails when the emitter writes an undeclared key.
`tests/unit/test_bindings.py` checks checked-in TypeScript and Python artifacts;
`tests/unit/test_conformance.py` checks the shared cross-language corpus. CI's
`bindings` job installs Go and Node and fails if those checks skip.

## 8. Other exports

`fastraml.views.jsonschema.to_json_schema(shape)` returns a JSON Schema draft-07
document and any information the export could not represent. The input shape
must be unwrapped.

`fastraml.views.openapi.to_openapi(raml)` returns an OpenAPI 3.0.3 document and
loss notices. `fastraml openapi` emits YAML by default or JSON with
`--format json`; notices go to stderr.

Both exports are read-only views. Their detailed mapping tests are
`tests/unit/test_jsonschema_view.py` and `tests/unit/test_openapi_view.py`.

## 9. Verification

- View boundary: `tests/unit/test_views.py`
- Graph and tree behavior: `tests/unit/test_graph.py`, `tests/unit/test_cli.py`
- Tree bindings: `tests/unit/test_bindings.py`, `tests/unit/test_conformance.py`
- Consumer traversal law: `tests/unit/test_consumer_traversal.py`

For durable design rationale that is not part of the current contract, see
`docs/archive/views-consumers-history.md`.
