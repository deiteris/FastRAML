"""pyRAML — a RAML 1.0 parser for Python.

The design is settled in `docs/`; start at `docs/README.md`.

Phase 0 of the implementation plan is complete: positions, diagnostics, URI
handling, resource loading and the YAML node model. The parser entry points
(`parse_from_path`, `parse_from_string`) arrive in later phases — see
`docs/15-implementation-plan.md`.
"""

from __future__ import annotations

from pyraml.errors import Accumulator, ErrorKind, RamlError, Trace
from pyraml.loaders import (
    FileLoader,
    HTTPLoader,
    LoaderError,
    ResourceLoader,
    SafeFileLoader,
    SchemeLoader,
    UnsupportedSchemeError,
    WorkspaceEscapeError,
    build_loader,
)
from pyraml.positions import Position
from pyraml.uris import file_uri_to_path, path_to_file_uri, resolve_uri_ref
from pyraml.yamlnode import Node, NodeKind, backend_name, compose

__version__ = '0.0.1'

__all__ = [
    'Accumulator',
    'ErrorKind',
    'FileLoader',
    'HTTPLoader',
    'LoaderError',
    'Node',
    'NodeKind',
    'Position',
    'RamlError',
    'ResourceLoader',
    'SafeFileLoader',
    'SchemeLoader',
    'Trace',
    'UnsupportedSchemeError',
    'WorkspaceEscapeError',
    '__version__',
    'backend_name',
    'build_loader',
    'compose',
    'file_uri_to_path',
    'path_to_file_uri',
    'resolve_uri_ref',
]
