"""Views of the finished model (docs/16-graph.md).

Nothing here is a pass. Every module runs after the parser has finished,
decides no RAML rule, and reads the model without writing to it. Nothing under
`fastraml/parser/` or `fastraml/types/` may import this package;
`tests/unit/test_views.py` asserts it (docs/16 § 1).

    walk        one traversal, one address per entity; graph and tree share it (§ 2)
    severity    the ranking arithmetic `backward` and `lint` both need
    graph       the model as nodes and edges: identity and reference (§ 3)
    queries     the SPARQL catalogue over `graph` (§ 3.1)
    render      one type, endpoint or operation as RAML-shaped text (§ 4)
    backward/   two effective API models compared for caller compatibility (§ 5)
    tree        the model as containment: the effective document tree (§ 6)
    bindings/   TypeScript, Python and Go bindings for the tree contract (§ 7)
    jsonschema  one shape as JSON Schema draft-07 (§ 8)
    openapi     the effective API as OpenAPI 3.0.3 (§ 8)
    lint/       policy rules over the effective model (docs/18-linting.md)

`walk` is the shared substrate rather than a view of its own: `graph` and `tree`
are lossy on orthogonal axes, and both are addressed by the same walk so a node
in one is joinable with the same entity in the other.

`severity` is substrate too, and holds less than it looks. `backward` and `lint`
grade on **different axes** — what a change does to a caller against how much a
finding should block CI — and those are deliberately not merged (docs/18 § 1).
What they share is the arithmetic: worst-first, and "this grade and everything
worse". A `Ranking` is that, given the vocabulary as data, so neither view can
drift from the other's idea of an ordering and neither has to adopt the other's
idea of a grade.

`bindings` reads no model at all: it emits, from `bindings/schema.py`, the
declarations a consumer needs to read what `tree` writes. It lives here because
it describes this layer's output.

Importing this package imports nothing: each module is imported by name, and
`queries` needs `pyoxigraph` only to *run* a query, not to hold one.
"""

from __future__ import annotations

__all__: list[str] = []
