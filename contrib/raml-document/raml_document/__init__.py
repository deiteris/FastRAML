"""A typed model of a RAML 1.0 document, for anything that emits one.

The RAML spelling of every facet is stated here once -- the `camelCase` names,
the order keys appear in, the shorthand that writes `title: string` rather than
`title: {type: string}` -- so an emitter builds a typed structure and never
formats RAML itself.

Depends on `yaml` and nothing else. Not on a web framework, and not on `fastraml`:
this is the authoring side, and a parser is on the other one. `fastapi-raml` and
`aiohttp-raml` both build on it, and a third integration would need no more of
it than they do.

`raml_document.serve` is the one module that needs more: it parses a rendered
document back with `fastraml` (the `serve` extra), which every integration
does before serving one.
"""

from __future__ import annotations

from raml_document.model import (
    UNSET,
    Body,
    Document,
    Documentation,
    Method,
    Parameters,
    Resource,
    Response,
    SecuredBy,
    SecurityScheme,
    TypeDecl,
    Unset,
    Yaml,
)
from raml_document.report import Report

__all__ = [
    'UNSET',
    'Body',
    'Document',
    'Documentation',
    'Method',
    'Parameters',
    'Report',
    'Resource',
    'Response',
    'SecuredBy',
    'SecurityScheme',
    'TypeDecl',
    'Unset',
    'Yaml',
]
