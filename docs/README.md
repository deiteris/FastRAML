# fastRAML documentation

fastRAML is a RAML 1.0 parser and model-projection toolkit for Python 3.12 and
later. The numbered documents in this directory define the current behavior and
architecture. The implementation and these documents must agree.

## Choose a starting point

### Use the library or CLI

- [Scope and spec coverage](01-scope-and-coverage.md) lists supported RAML
  features and deliberate deviations.
- [Public API](13-public-api.md) documents Python entry points, options, model
  contracts, and CLI commands.
- [Validation](10-validation.md) and [diagnostics](11-diagnostics.md) explain how
  fastRAML checks documents and reports failures.
- [Views](16-graph.md) documents graph, tree, rendering, compatibility, binding,
  JSON Schema, and OpenAPI projections.

### Understand or change the parser

1. Read [Architecture](02-architecture.md) for the pass pipeline, package map,
   registry, and invariants.
2. Read the document that owns the area you will change. The architecture
   document maps modules to their owning documents.
3. Update the owning document with any behavior change. A documented rule that
   differs from the implementation is a defect.
4. Use [Testing strategy](14-testing.md) and [Performance](12-performance.md) to
   select the required checks.

### Work on views and consumers

- [Views](16-graph.md) defines the read-only layer over a completed parse.
- [Consumers](17-consumers.md) defines repository boundaries for `viewer/`,
  `contrib/`, generated bindings, and shared fixtures.
- [Linting](18-linting.md) defines policy checks that run above the parser.

## Normative documents

| # | Document | Current contract |
|---|---|---|
| 01 | [Scope and spec coverage](01-scope-and-coverage.md) | Supported RAML 1.0 features, deviations, and non-goals |
| 02 | [Architecture](02-architecture.md) | Parse passes, package ownership, registry, and invariants |
| 03 | [YAML layer and I/O](03-yaml-and-io.md) | Nodes, source positions, includes, loaders, URIs, and workspace safety |
| 04 | [Fragments and namespaces](04-fragments-and-namespaces.md) | Fragment kinds, libraries, reference resolution, and lexical scope |
| 05 | [Type model](05-type-model.md) | Shapes, facets, inference, declaration decoding, and examples |
| 06 | [Type expressions](06-type-expressions.md) | Type-expression grammar, parser, and shape construction |
| 07 | [Resolution and inheritance](07-resolution-and-inheritance.md) | Reference resolution, inheritance, aliases, unwrap, and recursion |
| 08 | [Templates and endpoints](08-templates-and-endpoints.md) | Endpoint construction, structural merge, traits, resource types, and provenance |
| 09 | [Security and annotations](09-security-and-annotations.md) | Security schemes, inheritance, annotation types, and domain extensions |
| 10 | [Validation](10-validation.md) | Declaration checks, value validation, custom facets, and JSON Schema |
| 11 | [Diagnostics](11-diagnostics.md) | Error chains, accumulation, source attribution, and rendering |
| 12 | [Performance](12-performance.md) | Complexity requirements, hot paths, recursion limits, and benchmarks |
| 13 | [Public API](13-public-api.md) | Python exports, parser options, model contracts, and CLI |
| 14 | [Testing strategy](14-testing.md) | Unit, corpus, conformance, golden, property, and benchmark tests |
| 15 | [Status and roadmap](15-implementation-plan.md) | Non-normative parser status, deferred work, and future possibilities |
| 16 | [Views](16-graph.md) | Shared traversal, graph and tree projections, queries, rendering, compatibility, bindings, JSON Schema, and OpenAPI |
| 17 | [Consumers](17-consumers.md) | Consumer boundaries, generated artifacts, fixtures, and independent gates |
| 18 | [Linting](18-linting.md) | Rule engine, built-in policy, configuration, plugins, and output |

## Supporting material

- `archive/` contains retired design evidence and phase briefs that remain useful
  for explaining past decisions. It is not normative.
- `research/` contains open investigations. Nothing there defines parser
  behavior or may be required by the implementation.

Current parser work is complete. Deferred work is listed in the non-normative
[status and roadmap](15-implementation-plan.md). Conformance status belongs in
[the testing strategy](14-testing.md), not in this index.

## Conventions

- **MUST**, **SHOULD**, and **MAY** use their RFC 2119 meanings, as they do in the
  RAML specification.
- Citations such as *spec section Includes* refer to the
  [RAML 1.0 specification](https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md).
- A reference to [go-raml](https://github.com/acronis/go-raml) records the
  compatibility source for a rule when the RAML specification is silent. It
  does not make go-raml authoritative.
