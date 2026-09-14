"""Views of the finished model — docs/16-graph.md.

Nothing here is a pass. Every module below runs after P10 on a model the parser
has already finished, decides no RAML rule, and reads the model without writing
to it. **Nothing under `fastraml/parser/` or `fastraml/types/` may import this
package**, and `tests/unit/test_views.py` asserts it: a rule that belongs to the
language belongs in a pass, and an import in that direction is how it would
quietly stop being one.

The package boundary is the layer boundary, which is why `queries` and `diff`
live here too. They consume a `Graph` rather than the model, but they are on the
same side of the line and a package that held only some of it would not be worth
enforcing.

    walk        one traversal, one address per entity; every view below shares it
    graph       the model as a node set — identity and reference (§ 3 to § 5)
    tree        the model as containment — what is here (§ 11)
    render      one type or endpoint as text, for reading (§ 9)
    queries     the SPARQL catalogue over `graph` (§ 6)
    diff        two versions compared, and what breaks (§ 10)
    bindings    the tree's contract as TypeScript declarations (§ 11.11)

`walk` is the shared substrate rather than a view of its own: `graph` and `tree`
are lossy on orthogonal axes, and both are addressed by the same walk so a node
in one is joinable with the same entity in the other (§ 4).

`bindings` is the odd one: it reads no model at all, only the *source* of
`tree` and of the kind classes, and emits the declarations a consumer outside
Python needs in order to read what `tree` writes. It is here because it
describes this layer's output and belongs on this side of the line.

Importing this package imports nothing: each module is imported by name, and
`queries` needs `pyoxigraph` only to *run* a query, not to hold one.
"""

from __future__ import annotations

__all__: list[str] = []
