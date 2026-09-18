"""fastRAML — a RAML 1.0 parser for Python.

```python
from fastraml import ParseOptions, parse_from_path

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

__version__ = '0.1.0'

# Importing the public package is intentionally cheap. Each value is loaded and
# cached on first access; `fastraml/__init__.pyi` gives type checkers the same
# surface without making those imports happen at runtime.
_EXPORTS = {
    'APIFragment': ('fastraml.parser.fragments', 'APIFragment'),
    'Accumulator': ('fastraml.errors', 'Accumulator'),
    'AnyShape': ('fastraml.types.scalars', 'AnyShape'),
    'ApiChanged': ('fastraml.views.backward', 'ApiChanged'),
    'ApiSchemaChanged': ('fastraml.views.backward', 'ApiSchemaChanged'),
    'ArrayShape': ('fastraml.types.complex_', 'ArrayShape'),
    'BaseShape': ('fastraml.types.base', 'BaseShape'),
    'Body': ('fastraml.parser.endpoints', 'Body'),
    'BooleanShape': ('fastraml.types.scalars', 'BooleanShape'),
    'Conversion': ('fastraml.views.jsonschema', 'Conversion'),
    'CompatibilityConfig': ('fastraml.config', 'CompatibilityConfig'),
    'CompatibilityMatch': ('fastraml.config', 'CompatibilityMatch'),
    'CompatibilityRuleSetting': ('fastraml.config', 'CompatibilityRuleSetting'),
    'DataNode': ('fastraml.datanode', 'DataNode'),
    'DataTypeFragment': ('fastraml.parser.fragments', 'DataTypeFragment'),
    'DateOnlyShape': ('fastraml.types.scalars', 'DateOnlyShape'),
    'DateTimeOnlyShape': ('fastraml.types.scalars', 'DateTimeOnlyShape'),
    'DateTimeShape': ('fastraml.types.scalars', 'DateTimeShape'),
    'DocumentationItem': ('fastraml.parser.documentation', 'DocumentationItem'),
    'DocumentationItemFragment': ('fastraml.parser.fragments', 'DocumentationItemFragment'),
    'DomainExtension': ('fastraml.parser.annotations', 'DomainExtension'),
    'Addresses': ('fastraml.views.walk', 'Addresses'),
    'Edge': ('fastraml.views.graph', 'Edge'),
    'Entity': ('fastraml.nodes', 'Entity'),
    'EndPoint': ('fastraml.parser.endpoints', 'EndPoint'),
    'ErrorKind': ('fastraml.errors', 'ErrorKind'),
    'FileLoader': ('fastraml.loaders', 'FileLoader'),
    'FileShape': ('fastraml.types.scalars', 'FileShape'),
    'FastRamlConfig': ('fastraml.config', 'FastRamlConfig'),
    'Fragment': ('fastraml.parser.fragments', 'Fragment'),
    'FragmentKind': ('fastraml.parser.fragments', 'FragmentKind'),
    'Graph': ('fastraml.views.graph', 'Graph'),
    'to_json_schema': ('fastraml.views.jsonschema', 'to_json_schema'),
    'to_openapi': ('fastraml.views.openapi', 'to_openapi'),
    'GraphNode': ('fastraml.nodes', 'GraphNode'),
    'HTTPLoader': ('fastraml.loaders', 'HTTPLoader'),
    'IncludeInfo': ('fastraml.parser.includes', 'IncludeInfo'),
    'IncludeRef': ('fastraml.parser.includes', 'IncludeRef'),
    'IntegerShape': ('fastraml.types.scalars', 'IntegerShape'),
    'ItemsSegment': ('fastraml.views.backward', 'ItemsSegment'),
    'JsonShape': ('fastraml.types.jsonschema_', 'JsonShape'),
    'Library': ('fastraml.parser.fragments', 'Library'),
    'LoaderError': ('fastraml.loaders', 'LoaderError'),
    'NamedExample': ('fastraml.parser.fragments', 'NamedExample'),
    'NilShape': ('fastraml.types.scalars', 'NilShape'),
    'Node': ('fastraml.yamlnode', 'Node'),
    'NodeKind': ('fastraml.yamlnode', 'NodeKind'),
    'NumberShape': ('fastraml.types.scalars', 'NumberShape'),
    'ObjectShape': ('fastraml.types.complex_', 'ObjectShape'),
    'OAS3Document': ('fastraml.views.openapi', 'OAS3Document'),
    'Operation': ('fastraml.parser.endpoints', 'Operation'),
    'OperationAdded': ('fastraml.views.backward', 'OperationAdded'),
    'OperationChanged': ('fastraml.views.backward', 'OperationChanged'),
    'OperationId': ('fastraml.views.backward', 'OperationId'),
    'OperationRemoved': ('fastraml.views.backward', 'OperationRemoved'),
    'PatternPropertySegment': ('fastraml.views.backward', 'PatternPropertySegment'),
    'ParseCtx': ('fastraml.registry', 'ParseCtx'),
    'ParseOptions': ('fastraml.parser.entry', 'ParseOptions'),
    'ParserConfig': ('fastraml.config', 'ParserConfig'),
    'PatternProperty': ('fastraml.types.base', 'PatternProperty'),
    'Position': ('fastraml.positions', 'Position'),
    'Parameter': ('fastraml.types.base', 'Parameter'),
    'Property': ('fastraml.types.base', 'Property'),
    'PropertySegment': ('fastraml.views.backward', 'PropertySegment'),
    'SchemaChanged': ('fastraml.views.backward', 'SchemaChanged'),
    'Raml': ('fastraml.registry', 'Raml'),
    'RamlError': ('fastraml.errors', 'RamlError'),
    'RecursiveShape': ('fastraml.types.complex_', 'RecursiveShape'),
    'Request': ('fastraml.parser.endpoints', 'Request'),
    'ResourceLoader': ('fastraml.loaders', 'ResourceLoader'),
    'ResourceTypeFragment': ('fastraml.parser.fragments', 'ResourceTypeFragment'),
    'Response': ('fastraml.parser.endpoints', 'Response'),
    'Route': ('fastraml.views.graph', 'Route'),
    'SafeFileLoader': ('fastraml.loaders', 'SafeFileLoader'),
    'ScalarFacet': ('fastraml.types.base', 'ScalarFacet'),
    'SchemeLoader': ('fastraml.loaders', 'SchemeLoader'),
    'SecuritySchemeFragment': ('fastraml.parser.fragments', 'SecuritySchemeFragment'),
    'StringShape': ('fastraml.types.scalars', 'StringShape'),
    'TimeOnlyShape': ('fastraml.types.scalars', 'TimeOnlyShape'),
    'Trace': ('fastraml.errors', 'Trace'),
    'TraitFragment': ('fastraml.parser.fragments', 'TraitFragment'),
    'UnionShape': ('fastraml.types.complex_', 'UnionShape'),
    'UnionMemberSegment': ('fastraml.views.backward', 'UnionMemberSegment'),
    'UnknownShape': ('fastraml.types.complex_', 'UnknownShape'),
    'UnsupportedSchemeError': ('fastraml.loaders', 'UnsupportedSchemeError'),
    'ValueNode': ('fastraml.datanode', 'ValueNode'),
    'WorkspaceEscapeError': ('fastraml.loaders', 'WorkspaceEscapeError'),
    'address': ('fastraml.views.walk', 'address'),
    'backend_name': ('fastraml.yamlnode', 'backend_name'),
    'backward': ('fastraml.views.backward', 'backward'),
    'backward_markdown': ('fastraml.views.backward', 'backward_markdown'),
    'build_graph': ('fastraml.views.graph', 'build_graph'),
    'build_loader': ('fastraml.loaders', 'build_loader'),
    'build_tree': ('fastraml.views.tree', 'build_tree'),
    'compose': ('fastraml.yamlnode', 'compose'),
    'file_uri_to_path': ('fastraml.uris', 'file_uri_to_path'),
    'parse_from_path': ('fastraml.parser.entry', 'parse_from_path'),
    'parse_from_string': ('fastraml.parser.entry', 'parse_from_string'),
    'parse_lenient': ('fastraml.parser.entry', 'parse_lenient'),
    'load_config': ('fastraml.config', 'load_config'),
    'path_to_file_uri': ('fastraml.uris', 'path_to_file_uri'),
    'resolve_uri_ref': ('fastraml.uris', 'resolve_uri_ref'),
    'render_compatibility_markdown': ('fastraml.views.backward', 'render_markdown'),
    'same_value': ('fastraml.types.values', 'same_value'),
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
    'Addresses',
    'AnyShape',
    'ApiChanged',
    'ApiSchemaChanged',
    'ArrayShape',
    'BaseShape',
    'Body',
    'BooleanShape',
    'CompatibilityConfig',
    'CompatibilityMatch',
    'CompatibilityRuleSetting',
    'Conversion',
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
    'FastRamlConfig',
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
    'ItemsSegment',
    'JsonShape',
    'Library',
    'LoaderError',
    'NamedExample',
    'NilShape',
    'Node',
    'NodeKind',
    'NumberShape',
    'OAS3Document',
    'ObjectShape',
    'Operation',
    'OperationAdded',
    'OperationChanged',
    'OperationId',
    'OperationRemoved',
    'Parameter',
    'ParseCtx',
    'ParseOptions',
    'ParserConfig',
    'PatternProperty',
    'PatternPropertySegment',
    'Position',
    'Property',
    'PropertySegment',
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
    'SchemaChanged',
    'SchemeLoader',
    'SecuritySchemeFragment',
    'StringShape',
    'TimeOnlyShape',
    'Trace',
    'TraitFragment',
    'UnionMemberSegment',
    'UnionShape',
    'UnknownShape',
    'UnsupportedSchemeError',
    'ValueNode',
    'WorkspaceEscapeError',
    '__version__',
    'address',
    'backend_name',
    'backward',
    'backward_markdown',
    'build_graph',
    'build_loader',
    'build_tree',
    'compose',
    'file_uri_to_path',
    'load_config',
    'parse_from_path',
    'parse_from_string',
    'parse_lenient',
    'path_to_file_uri',
    'render_compatibility_markdown',
    'resolve_uri_ref',
    'same_value',
    'to_json_schema',
    'to_openapi',
]
