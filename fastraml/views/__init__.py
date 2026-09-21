"""Views of the finished model — docs/16-graph.md.

Nothing here is a pass. Every module below runs after P10 on a model the parser
has already finished, decides no RAML rule, and reads the model without writing
to it. **Nothing under `fastraml/parser/` or `fastraml/types/` may import this
package**, and `tests/unit/test_views.py` asserts it: a rule that belongs to the
language belongs in a pass, and an import in that direction is how it would
quietly stop being one.

The package boundary is the layer boundary, which is why `queries` lives here
too. It consumes a `Graph` rather than the model, but it is on the same side of
the line and a package that held only some of it would not be worth enforcing.

    walk        one traversal, one address per entity; every view below shares it
    severity    the ranking arithmetic `backward` and `lint` both need (doc 18 § 1)
    graph       the model as a node set — identity and reference (§ 3 to § 5)
    tree        the model as containment — what is here (§ 11)
    render      one type or endpoint as text, for reading (§ 9)
    queries     the SPARQL catalogue over `graph` (§ 6)
    backward/   two effective API models compared for caller compatibility (§ 10)
    bindings    language bindings for the tree's contract (§ 11.11)
    openapi     the effective API as OpenAPI 3.0.3 (§ 13)

`walk` is the shared substrate rather than a view of its own: `graph` and `tree`
are lossy on orthogonal axes, and both are addressed by the same walk so a node
in one is joinable with the same entity in the other (§ 4).

`severity` is substrate too, and holds less than it looks. `backward` and `lint`
grade on **different axes** — what a change does to a caller against how much a
finding should block CI — and those are deliberately not merged (docs/18 § 1).
What they share is the arithmetic: worst-first, and "this grade and everything
worse". A `Ranking` is that, given the vocabulary as data, so neither view can
drift from the other's idea of an ordering and neither has to adopt the other's
idea of a grade.

`bindings` is the odd one: its backends read no model at all, only the *source*
of `tree` and of the kind classes, and emit the declarations a consumer needs in
order to read what `tree` writes -- in TypeScript, in Python and in Go, because
the boundary a type checker fails to span is the JSON and not the language. It is
here because it describes this layer's output and belongs on this side of the
line.

Importing this package imports nothing: each module is imported by name, and
`queries` needs `pyoxigraph` only to *run* a query, not to hold one.
"""

from __future__ import annotations

__all__: list[str] = []
