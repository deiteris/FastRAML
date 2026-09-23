"""The `python-fastapi` target: a server interface to implement.

The mirror of `python-httpx`. That one reads a document and calls an API; this
one reads the same document and states the API to be written — pydantic DTOs,
one abstract class per path group, and routes that enforce every facet before a
handler runs.

Three modules. `annotate.py` decides what one shape is in pydantic; `plan.py`
adds the one reading only a server needs; `emit.py` builds each declaration as
source and renders the templates. The reading of the tree is in `../shared/`.

None of it states a RAML rule: everything the language says already ran in
the parser (docs/17 § 1).
"""

from __future__ import annotations

from .emit import generate_fastapi

__all__ = ['generate_fastapi']
