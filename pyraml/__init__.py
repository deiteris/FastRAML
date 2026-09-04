"""pyRAML — a RAML 1.0 parser for Python.

```python
from pyraml import ParseOptions, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True, validate=True))
api = raml.entry_point
```

Pass `unwrap=True, validate=True` together unless you specifically want to
inspect un-flattened declarations: `validate=True` alone has to unwrap a private
copy of every type it checks, and measures slower for it.

`parse_lenient` returns `(model, error)` instead of raising, for an editor that
needs a partial model on every keystroke.

**Four contracts a consumer must honour**, each detailed in
`docs/13-public-api.md` section 7:

1. The model may be cyclic — track visited ids in any traversal.
2. It is mutable and unguarded; the parser hands out its own objects.
3. One `Raml` instance is single-threaded.
4. Without `unwrap=True`, a shape shows only what its own declaration wrote.

**The API is not stable.** Until 1.0 anything here may be renamed or removed.
What is exported is what a consumer needs to walk and narrow the model; the
rest is reachable through its own module and is listed in
`docs/13-public-api.md` section 4.

The design is settled in `docs/`, which is normative; start at `docs/README.md`.
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
from pyraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
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
from pyraml.types.base import BaseShape, PatternProperty, Property, ScalarFacet

# The seventeen concrete kinds. Exported because `isinstance` against them is
# the documented way to narrow a shape (docs/13 section 6), and reaching into
# `pyraml.types.complex_` for that is a poor advertisement for a supported API.
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape, UnknownShape
from pyraml.types.jsonschema_ import JsonShape
from pyraml.types.scalars import (
    AnyShape,
    BooleanShape,
    DateOnlyShape,
    DateTimeOnlyShape,
    DateTimeShape,
    FileShape,
    IntegerShape,
    NilShape,
    NumberShape,
    StringShape,
    TimeOnlyShape,
)
from pyraml.uris import file_uri_to_path, path_to_file_uri, resolve_uri_ref
from pyraml.yamlnode import Node, NodeKind, backend_name, compose

__version__ = '0.0.1'

__all__ = [
    'APIFragment',
    'Accumulator',
    'AnyShape',
    'ArrayShape',
    'BaseShape',
    'Body',
    'BooleanShape',
    'DataNode',
    'DataTypeFragment',
    'DateOnlyShape',
    'DateTimeOnlyShape',
    'DateTimeShape',
    'DocumentationItem',
    'DocumentationItemFragment',
    'DomainExtension',
    'EndPoint',
    'ErrorKind',
    'FileLoader',
    'FileShape',
    'Fragment',
    'FragmentKind',
    'HTTPLoader',
    'IncludeInfo',
    'IncludeRef',
    'IntegerShape',
    'JsonShape',
    'Library',
    'LoaderError',
    'NamedExample',
    'NilShape',
    'Node',
    'NodeKind',
    'NumberShape',
    'ObjectShape',
    'Operation',
    'ParseCtx',
    'ParseOptions',
    'PatternProperty',
    'Position',
    'Property',
    'Raml',
    'RamlError',
    'RecursiveShape',
    'Request',
    'ResourceLoader',
    'ResourceTypeFragment',
    'Response',
    'SafeFileLoader',
    'ScalarFacet',
    'SchemeLoader',
    'SecuritySchemeFragment',
    'StringShape',
    'TimeOnlyShape',
    'Trace',
    'TraitFragment',
    'UnionShape',
    'UnknownShape',
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
