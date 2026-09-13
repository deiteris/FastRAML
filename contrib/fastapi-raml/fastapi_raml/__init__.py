"""Render a FastAPI application as RAML 1.0.

A consumer of `pyraml`, not part of it. `docs/01-scope-and-coverage.md` § 2 keeps
code generation and format converters out of the package, and this one also
depends on `fastapi` and `pydantic`, which the parser must not.

Two parts here, and a third shared:

- `render` reads a FastAPI app -- its collected models and its routes -- and
  builds a `raml_document.Document`.
- `serve` adds the routes that publish it, parsing the result back through
  pyRAML before anything is served.
- `raml-document` holds the RAML model itself, and knows about neither FastAPI
  nor pyRAML. `aiohttp-raml` builds on the same one; what differs between
  integrations is reading an application, not writing RAML.

`tests/test_differential.py` is the gate: the rendered RAML must give the same
verdict on a payload as the pydantic model it was rendered from.
"""

from __future__ import annotations

from fastapi_raml.render import Report, render
from fastapi_raml.serve import Served, add_raml_routes, build

__all__ = ['Report', 'Served', 'add_raml_routes', 'build', 'render']
