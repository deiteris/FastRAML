"""pyRAML — a RAML 1.0 parser for Python.

The design is settled in `docs/`; start at `docs/README.md`.

Phases 0 and 1 of the implementation plan are complete: positions, diagnostics,
URI handling, resource loading, the YAML node model, and the fragment layer —
fragments, `!include`, `uses:` and namespaces. Type declarations are retained as
source and decoded from Phase 2 on; see `docs/15-implementation-plan.md`.

Three contracts a consumer must honour, each detailed in
`docs/13-public-api.md` section 7: the model may be cyclic, it is mutable and
unguarded, and one `Raml` instance is single-threaded.
"""

from __future__ import annotations

from pyraml.datanode import DataNode, ValueNode
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
from pyraml.parser.annotations import DomainExtension
from pyraml.parser.documentation import DocumentationItem
from pyraml.parser.entry import ParseOptions, parse_from_path, parse_from_string, parse_lenient
from pyraml.parser.fragments import (
    APIFragment,
    DataTypeFragment,
    DocumentationItemFragment,
    Fragment,
    FragmentKind,
    Library,
    NamedExample,
    ResourceTypeFragment,
    SecuritySchemeFragment,
    TraitFragment,
)
from pyraml.parser.includes import IncludeInfo, IncludeRef
from pyraml.positions import Position
from pyraml.registry import ParseCtx, Raml
from pyraml.types.base import ScalarFacet
from pyraml.uris import file_uri_to_path, path_to_file_uri, resolve_uri_ref
from pyraml.yamlnode import Node, NodeKind, backend_name, compose

__version__ = '0.0.1'

__all__ = [
    'APIFragment',
    'Accumulator',
    'DataNode',
    'DataTypeFragment',
    'DocumentationItem',
    'DocumentationItemFragment',
    'DomainExtension',
    'ErrorKind',
    'FileLoader',
    'Fragment',
    'FragmentKind',
    'HTTPLoader',
    'IncludeInfo',
    'IncludeRef',
    'Library',
    'LoaderError',
    'NamedExample',
    'Node',
    'NodeKind',
    'ParseCtx',
    'ParseOptions',
    'Position',
    'Raml',
    'RamlError',
    'ResourceLoader',
    'ResourceTypeFragment',
    'SafeFileLoader',
    'ScalarFacet',
    'SchemeLoader',
    'SecuritySchemeFragment',
    'Trace',
    'TraitFragment',
    'UnsupportedSchemeError',
    'ValueNode',
    'WorkspaceEscapeError',
    '__version__',
    'backend_name',
    'build_loader',
    'compose',
    'file_uri_to_path',
    'parse_from_path',
    'parse_from_string',
    'parse_lenient',
    'path_to_file_uri',
    'resolve_uri_ref',
]
