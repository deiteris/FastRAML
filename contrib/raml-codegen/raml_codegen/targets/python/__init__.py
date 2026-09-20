"""The targets that write Python, and the part of them that is the same.

A target name is `<language>-<library>`, and the module path follows it:
`python-httpx` is `targets.python.httpx` and `python-fastapi` is
`targets.python.fastapi`. A second language would be a sibling of this package,
with a `shared/` of its own.

`shared/` holds everything that is not a spelling — descending the tree,
following a link, stopping at a recursion marker, claiming a name for an
anonymous shape — plus the two parts that are Python's rather than any one
library's: a RAML `description:` as a docstring, and a module's import block.

The two targets read the same document in opposite directions. `httpx` writes
the caller; `fastapi` writes the thing being called. They disagree in exactly
one place — a facet is documentation to a client and a constraint to a server —
and `docs/17` § 5.3 says why.
"""

from __future__ import annotations

__all__: list[str] = []
