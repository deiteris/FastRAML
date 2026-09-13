# pyRAML — parser design documents

A RAML 1.0 parser for Python, modelled on [go-raml](https://github.com/acronis/go-raml)
(reference source: `C:\Sources\go-raml-main`) and the
[RAML 1.0 specification](https://github.com/raml-org/raml-spec/blob/master/versions/raml-10/raml-10.md)
(local copy: `C:\Sources\raml-spec\versions\raml-10\raml-10.md`).

These documents describe **what to build and why**, before any code is written.
They are normative for the implementation: where a document states a rule, the code
follows it or the document is amended first.

## Reading order

| # | Document | What it settles |
|---|----------|-----------------|
| 01 | [Scope and spec coverage](01-scope-and-coverage.md) | Which parts of RAML 1.0 are in scope, deliberate deviations, non-goals |
| 02 | [Architecture](02-architecture.md) | The parse pipeline, package layout, the central registry, cross-cutting invariants |
| 03 | [YAML layer and I/O](03-yaml-and-io.md) | Node model, source positions, `!include`, resource loaders, URI/workspace rules |
| 04 | [Fragments and namespaces](04-fragments-and-namespaces.md) | Fragment kinds, `uses:`, reference resolution, lexical anchor scoping |
| 05 | [Type model (shapes)](05-type-model.md) | `BaseShape`, facets, per-type shapes, default-type inference, decoding |
| 06 | [Type expressions](06-type-expressions.md) | The RDT grammar, hand-written tokenizer/parser, expression→shape visitor |
| 07 | [Resolution and inheritance](07-resolution-and-inheritance.md) | The unresolved worklist, unwrap, inherit rules, aliases, recursion marking |
| 08 | [Templates and endpoints](08-templates-and-endpoints.md) | Two-stage endpoint build, structural merge, provenance overlay, traits/resource types |
| 09 | [Security schemes and annotations](09-security-and-annotations.md) | `securitySchemes`, `securedBy`, annotation types, domain extensions |
| 10 | [Validation](10-validation.md) | `check` vs `validate`, examples/defaults/enums, facet validation, JSON Schema |
| 11 | [Diagnostics](11-diagnostics.md) | Error model, accumulation, partial-result tolerance, positions |
| 12 | [Performance](12-performance.md) | Every performance technique go-raml uses, and its Python translation |
| 13 | [Public API](13-public-api.md) | Entry points, options, the model surface consumers see |
| 14 | [Testing strategy](14-testing.md) | TCK integration, unit/golden/property/benchmark layers |
| 15 | [Implementation plan](15-implementation-plan.md) | Milestones, ordering, definition of done per phase |
| 16 | [The views](16-graph.md) | `pyraml/views/`: one addressing walk, the model as a graph and as a tree, RDF/SPARQL, why not AMF, the reading view, the version diff |
| 17 | [Consumers](17-consumers.md) | `viewer/`, `contrib/` and `fixtures/`: the one-way boundary, what may not live there, and the gates CI runs |

## Research

`research/` holds open questions: a problem, what was measured about it, which
routes were tried and why they failed. **Nothing there is normative and nothing
in the code depends on it** — a question that gets settled moves into the
numbered documents above and leaves a pointer behind.

| Document | The question |
|----------|--------------|
| [Document identity](research/document-identity.md) | A document's identity is its retrieval path, so two copies of a library are two libraries. RAML offers no `$id` and no inference rule; what can a parser supply, and what stays out of reach? |

## Conventions in these documents

- **MUST / SHOULD / MAY** carry RFC 2119 meaning, as in the RAML spec itself.
- Citations of the form *spec § Section Name* refer to `raml-10.md`.
- Citations of the form `go-raml:file.go` refer to the reference implementation.
- Python identifiers are `snake_case`; the Go names are given where a reader may
  want to diff against the reference.
