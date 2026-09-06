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

from importlib import import_module

__version__ = '0.0.1'

# Importing the public package is intentionally cheap. Each value is loaded and
# cached on first access; `pyraml/__init__.pyi` gives type checkers the same
# surface without making those imports happen at runtime.
_EXPORTS = {
    'APIFragment': ('pyraml.parser.fragments', 'APIFragment'),
    'Accumulator': ('pyraml.errors', 'Accumulator'),
    'AnyShape': ('pyraml.types.scalars', 'AnyShape'),
    'ArrayShape': ('pyraml.types.complex_', 'ArrayShape'),
    'BaseShape': ('pyraml.types.base', 'BaseShape'),
    'Body': ('pyraml.parser.endpoints', 'Body'),
    'BooleanShape': ('pyraml.types.scalars', 'BooleanShape'),
    'DataNode': ('pyraml.datanode', 'DataNode'),
    'DataTypeFragment': ('pyraml.parser.fragments', 'DataTypeFragment'),
    'DateOnlyShape': ('pyraml.types.scalars', 'DateOnlyShape'),
    'DateTimeOnlyShape': ('pyraml.types.scalars', 'DateTimeOnlyShape'),
    'DateTimeShape': ('pyraml.types.scalars', 'DateTimeShape'),
    'DocumentationItem': ('pyraml.parser.documentation', 'DocumentationItem'),
    'DocumentationItemFragment': ('pyraml.parser.fragments', 'DocumentationItemFragment'),
    'DomainExtension': ('pyraml.parser.annotations', 'DomainExtension'),
    'Edge': ('pyraml.graph', 'Edge'),
    'Entity': ('pyraml.graph', 'Entity'),
    'EndPoint': ('pyraml.parser.endpoints', 'EndPoint'),
    'ErrorKind': ('pyraml.errors', 'ErrorKind'),
    'FileLoader': ('pyraml.loaders', 'FileLoader'),
    'FileShape': ('pyraml.types.scalars', 'FileShape'),
    'Fragment': ('pyraml.parser.fragments', 'Fragment'),
    'FragmentKind': ('pyraml.parser.fragments', 'FragmentKind'),
    'Graph': ('pyraml.graph', 'Graph'),
    'GraphNode': ('pyraml.graph', 'GraphNode'),
    'HTTPLoader': ('pyraml.loaders', 'HTTPLoader'),
    'IncludeInfo': ('pyraml.parser.includes', 'IncludeInfo'),
    'IncludeRef': ('pyraml.parser.includes', 'IncludeRef'),
    'IntegerShape': ('pyraml.types.scalars', 'IntegerShape'),
    'JsonShape': ('pyraml.types.jsonschema_', 'JsonShape'),
    'Library': ('pyraml.parser.fragments', 'Library'),
    'LoaderError': ('pyraml.loaders', 'LoaderError'),
    'NamedExample': ('pyraml.parser.fragments', 'NamedExample'),
    'NilShape': ('pyraml.types.scalars', 'NilShape'),
    'Node': ('pyraml.yamlnode', 'Node'),
    'NodeKind': ('pyraml.yamlnode', 'NodeKind'),
    'NumberShape': ('pyraml.types.scalars', 'NumberShape'),
    'ObjectShape': ('pyraml.types.complex_', 'ObjectShape'),
    'Operation': ('pyraml.parser.endpoints', 'Operation'),
    'ParseCtx': ('pyraml.registry', 'ParseCtx'),
    'ParseOptions': ('pyraml.parser.entry', 'ParseOptions'),
    'PatternProperty': ('pyraml.types.base', 'PatternProperty'),
    'Position': ('pyraml.positions', 'Position'),
    'Parameter': ('pyraml.types.base', 'Parameter'),
    'Property': ('pyraml.types.base', 'Property'),
    'Raml': ('pyraml.registry', 'Raml'),
    'RamlError': ('pyraml.errors', 'RamlError'),
    'RecursiveShape': ('pyraml.types.complex_', 'RecursiveShape'),
    'Request': ('pyraml.parser.endpoints', 'Request'),
    'ResourceLoader': ('pyraml.loaders', 'ResourceLoader'),
    'ResourceTypeFragment': ('pyraml.parser.fragments', 'ResourceTypeFragment'),
    'Response': ('pyraml.parser.endpoints', 'Response'),
    'Route': ('pyraml.graph', 'Route'),
    'SafeFileLoader': ('pyraml.loaders', 'SafeFileLoader'),
    'ScalarFacet': ('pyraml.types.base', 'ScalarFacet'),
    'SchemeLoader': ('pyraml.loaders', 'SchemeLoader'),
    'SecuritySchemeFragment': ('pyraml.parser.fragments', 'SecuritySchemeFragment'),
    'StringShape': ('pyraml.types.scalars', 'StringShape'),
    'TimeOnlyShape': ('pyraml.types.scalars', 'TimeOnlyShape'),
    'Trace': ('pyraml.errors', 'Trace'),
    'TraitFragment': ('pyraml.parser.fragments', 'TraitFragment'),
    'UnionShape': ('pyraml.types.complex_', 'UnionShape'),
    'UnknownShape': ('pyraml.types.complex_', 'UnknownShape'),
    'UnsupportedSchemeError': ('pyraml.loaders', 'UnsupportedSchemeError'),
    'ValueNode': ('pyraml.datanode', 'ValueNode'),
    'WorkspaceEscapeError': ('pyraml.loaders', 'WorkspaceEscapeError'),
    'backend_name': ('pyraml.yamlnode', 'backend_name'),
    'build_graph': ('pyraml.graph', 'build_graph'),
    'build_loader': ('pyraml.loaders', 'build_loader'),
    'compose': ('pyraml.yamlnode', 'compose'),
    'file_uri_to_path': ('pyraml.uris', 'file_uri_to_path'),
    'parse_from_path': ('pyraml.parser.entry', 'parse_from_path'),
    'parse_from_string': ('pyraml.parser.entry', 'parse_from_string'),
    'parse_lenient': ('pyraml.parser.entry', 'parse_lenient'),
    'path_to_file_uri': ('pyraml.uris', 'path_to_file_uri'),
    'resolve_uri_ref': ('pyraml.uris', 'resolve_uri_ref'),
}


def __getattr__(name: str) -> object:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}') from None
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


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
    'Edge',
    'EndPoint',
    'Entity',
    'ErrorKind',
    'FileLoader',
    'FileShape',
    'Fragment',
    'FragmentKind',
    'Graph',
    'GraphNode',
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
    'Parameter',
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
    'Route',
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
    'build_graph',
    'build_loader',
    'compose',
    'file_uri_to_path',
    'parse_from_path',
    'parse_from_string',
    'parse_lenient',
    'path_to_file_uri',
    'resolve_uri_ref',
]
