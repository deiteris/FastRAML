"""Render a FastAPI application as RAML 1.0.

A consumer of `pyraml`, not part of it. `docs/01-scope-and-coverage.md` § 2 keeps
code generation and format converters out of the package, and this one also
depends on `fastapi` and `pydantic`, which the parser must not.

Three parts, and the split is the point:

- `document` is a typed model of the RAML a writer emits, and imports neither
  FastAPI nor pyRAML. It states the RAML spelling of every facet once.
- `render` reads a FastAPI app -- its collected models and its routes -- and
  builds one.
- `serve` adds the routes that publish it, parsing the result back through
  pyRAML before anything is served.

`differential.py` is the gate: the rendered RAML must give the same verdict on a
payload as the pydantic model it was rendered from.
"""

from __future__ import annotations

from fastapi_raml.document import Document, TypeDecl
from fastapi_raml.render import Report, render
from fastapi_raml.serve import Served, add_raml_routes, build

__all__ = ['Document', 'Report', 'Served', 'TypeDecl', 'add_raml_routes', 'build', 'render']
