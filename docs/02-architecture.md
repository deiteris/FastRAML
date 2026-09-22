# 02 - Architecture

## 1. Parse pipeline

Parsing uses one `Raml` registry and a fixed pass order.

| Pass | Responsibility |
|---|---|
| P0 | Identify the RAML fragment header. |
| P1 | Compose source into position-bearing `Node` trees and resolve data includes. |
| P2 | Decode fragments and declarations; retain endpoint source nodes. |
| P3 | Resolve `uses:` libraries recursively. |
| P4 | Build endpoints: merge source IR directives, then materialize the result. |
| P5 | Bind and inherit security schemes. |
| P6 | Propagate URI parameters. |
| P7 | Resolve type expressions and shape references. |
| P8 | Bind domain extensions to annotation types. |
| P9 | Optionally unwrap inheritance and mark recursion. |
| P10 | Optionally check declarations and validate examples, defaults, facets, and annotation values. |

P4 precedes P7 because template expansion can introduce type-bearing nodes. P7
precedes P9 because inheritance cannot merge an unresolved shape. P9 and P10
are optional parser passes: validation without public unwrapping uses private
unwrapped copies.

## 2. Package ownership

| Area | Modules | Owning document |
|---|---|---|
| Diagnostics, positions, URIs, loaders | `errors.py`, `positions.py`, `uris.py`, `loaders.py` | [03](03-yaml-and-io.md), [11](11-diagnostics.md) |
| YAML and arbitrary data | `yamlnode.py`, `datanode.py` | [03](03-yaml-and-io.md) |
| Parse state | `registry.py`, `domains.py` | this document, [04](04-fragments-and-namespaces.md) |
| Entry and RAML decoding | `parser/entry.py`, `parser/fragments.py`, `parser/includes.py`, `parser/references.py` | [03](03-yaml-and-io.md), [04](04-fragments-and-namespaces.md) |
| Endpoints and templates | `parser/source_ir.py`, `parser/structural_merge.py`, `parser/source_decode.py`, `parser/endpoint_build.py`, `parser/traits.py`, `parser/resourcetypes.py` | [08](08-templates-and-endpoints.md) |
| Security and annotations | `parser/security.py`, `parser/annotations.py`, `parser/directives.py` | [09](09-security-and-annotations.md) |
| Type system | `types/` | [05](05-type-model.md) through [10](10-validation.md) |
| Read-only projections | `views/`, including graph, tree, rendering, queries, compatibility, bindings, JSON Schema, OpenAPI, and linting | [16](16-graph.md), [18](18-linting.md) |
| CLI | `cli.py` | [13](13-public-api.md) |

`views/` is a consumer layer, not a parser pass. Nothing under `parser/` or
`types/` may import `fastraml.views`; outside `views/`, only `cli.py` may do so.
Views requiring effective types require their caller to provide an unwrapped
model; CLI commands do this through `ParseOptions(unwrap=True)`.

`registry.py` imports no parser or type module at runtime. `loaders.py` is the
only module that reads files or the network. The type layer may import
`parser/facets.py`, `parser/annotations.py`, and `parser/includes.py` at runtime;
`types/shape.py` has the sole deferred import that breaks the fragment/shape
cycle. These boundaries keep the runtime import graph acyclic.

## 3. The registry (`Raml`)

One `Raml` instance owns one parse. It holds:

- parse configuration and the scheme loader;
- fragment, include-node, expression, and JSON Schema caches;
- declaration, resolver, endpoint, shape, annotation, and include-reference
  indices;
- the unresolved-shape worklist and parse-context/provenance state; and
- optional retained source nodes, text, and entity-to-source information.

Every model entity receives a parse-local monotonically increasing integer ID.
IDs identify entities for clone memoization, source indexing, diagnostics, and
serialized views; they are not content hashes.

## 4. Cross-cutting invariants

| # | Invariant | Established by |
|---|---|---|
| I1 | Fragment and shape locations are `file://` or `http(s)://` URIs. | P1 |
| I2 | A source file is composed at most once per parse. | P1 |
| I3 | A fragment is decoded at most once per parse. | P2 |
| I4 | Every decoded shape is indexed; unresolved shapes enter the P7 worklist. | P2 |
| I5 | No reachable shape is `UnknownShape` after P7. | P7 |
| I6 | After P9, reachable shapes are unwrapped, have no `link`, and unions have dispatch tables. | P9 |
| I7 | Entities carry a URI location and 1-based source positions. | all |
| I8 | Model mappings preserve declaration order. | all |
| I9 | Structural merge mutates neither input node tree. | P4 |
| I10 | Structural merge preserves node identity for provenance lookup. | P4 |
| I11 | Addresses are assigned by the shared view traversal; an address can be shared by linked entities. | views |
| I12 | P10 validates unwrapped shapes, either public P9 results or private copies. | P10 |

## 5. Errors and recovery

Decoders accumulate independent local diagnostics and return partial models
where possible. Entry loading, an unknown or unsupported header, a fragment-kind
mismatch, and a non-mapping entry root are fatal because they leave no
trustworthy entry model. See [11](11-diagnostics.md).

## 6. Endpoint construction

Traits and resource types merge declaration trees, not typed endpoint objects.
Stage 1 decodes directives and retains endpoint source nodes. Structural merge
applies resource types and traits to those nodes. Stage 2 decodes each merged
node tree once into endpoints.

Merged nodes can originate in different files. A provenance overlay maps node
identity to its authored parse context so stage-2 decoding resolves names and
reports locations in the appropriate lexical scope. See
[08](08-templates-and-endpoints.md).
