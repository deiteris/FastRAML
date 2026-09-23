"""The `fastraml tree` contract.

Assembled by `python -m fastraml.views.bindings python`. Do not edit the
assembled file. Edit `fastraml/views/bindings/static/tree.pyi`, which holds the
aliases and the fixed records; everything from `ShapeType` on is generated from
`fastraml/views/tree.py` and the kind classes in `fastraml/types/`, so a facet
added to a kind arrives without either half being edited.
`tests/unit/test_bindings.py` fails when they disagree.

The metamodel is three constructs (docs/16-graph.md § 6.1):

    {"$ref": <address>}                            a link -- look the target up
    {"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
    anything else                                  containment -- descend

A consumer descends containment, follows a link when it chooses to, and stops at
a recursion marker. It maintains no ancestor set. Requires
`ParseOptions(unwrap=True)`, which `fastraml tree` uses.

Field annotations are quoted and `from __future__ import annotations` is absent,
which is deliberate and not a style. The contract is cyclic -- `ShapeBase.inherits`
holds `ShapeNode`, which is `Shape`, which is built from `ShapeBase` -- so some
forward reference is unavoidable. Turning on PEP 563 instead would make every
annotation a string before `TypedDict` reads it, and `NotRequired` inside a string
is invisible: `__required_keys__` would then report every key as required. Quoting
the *inside* of `NotRequired[...]` keeps both halves right.
"""

from typing import Final, Literal, NotRequired, TypeAlias, TypedDict

#: A structural address: stable across re-parses, and the identity of a node.
Address: TypeAlias = str

#: An exact decimal carried as text so no consumer rounds it through a float.
ExactDecimal: TypeAlias = str

JsonObject: TypeAlias = 'dict[str, Json]'
Json: TypeAlias = 'str | int | float | bool | None | list[Json] | JsonObject'

Protocol: TypeAlias = Literal['HTTP', 'HTTPS']
ParameterBinding: TypeAlias = Literal['uri', 'query', 'header']

#: The six the spec names, plus `x-<anything>`, which Python cannot spell as a
#: type. The closed half stays closed and the open half widens to `str`.
SecuritySchemeType: TypeAlias = (
    Literal[
        'null',
        'OAuth 1.0',
        'OAuth 2.0',
        'Basic Authentication',
        'Digest Authentication',
        'Pass Through',
    ]
    | str
)

SourceFile: TypeAlias = str
DeclarationName: TypeAlias = str
EndpointPath: TypeAlias = str
StatusCode: TypeAlias = str
MediaType: TypeAlias = str

#: A link. Its sole key is the test -- an expanded shape carries `id` as well.
#: The functional form, because `$ref` is not a Python identifier.
Ref = TypedDict('Ref', {'$ref': Address})

ShapeNode: TypeAlias = 'Shape | Ref | Recursion'
ShapeDeclarations: TypeAlias = 'dict[DeclarationName, Shape | Ref]'
ShapeDeclarationsByFile: TypeAlias = 'dict[SourceFile, ShapeDeclarations]'
SecuritySchemeDeclarations: TypeAlias = 'dict[DeclarationName, SecurityScheme]'
SecuritySchemeDeclarationsByFile: TypeAlias = 'dict[SourceFile, SecuritySchemeDeclarations]'
EndpointsByPath: TypeAlias = 'dict[EndpointPath, Endpoint]'
OperationsByMethod: TypeAlias = 'dict[HttpMethod, Operation]'
ResponsesByStatus: TypeAlias = 'dict[StatusCode, Response]'
BodiesByMediaType: TypeAlias = 'dict[MediaType, ShapeNode | None]'
SecuritySetting: TypeAlias = 'str | list[str]'
SecuritySettings: TypeAlias = 'dict[str, SecuritySetting]'


class DocumentationItem(TypedDict):
    title: str
    content: str


class Property(TypedDict):
    required: bool
    type: 'ShapeNode | None'


class PatternProperty(TypedDict):
    pattern: str
    type: 'ShapeNode | None'


class Parameter(TypedDict):
    binding: ParameterBinding
    required: bool
    type: 'ShapeNode | None'


ShapeType: TypeAlias = Literal[
    'any',
    'nil',
    'null',
    'boolean',
    'string',
    'integer',
    'number',
    'datetime',
    'datetime-only',
    'date-only',
    'time-only',
    'file',
    'object',
    'array',
    'union',
    'json',
]
HttpMethod: TypeAlias = Literal['connect', 'delete', 'get', 'head', 'options', 'patch', 'post', 'put', 'trace']
FragmentKind: TypeAlias = Literal[
    'API',
    'Library',
    'DataType',
    'AnnotationTypeDeclaration',
    'NamedExample',
    'DocumentationItem',
    'Trait',
    'ResourceType',
    'SecurityScheme',
]
AnnotationTarget: TypeAlias = Literal[
    'API',
    'DocumentationItem',
    'Resource',
    'Method',
    'Response',
    'RequestBody',
    'ResponseBody',
    'TypeDeclaration',
    'Example',
    'ResourceType',
    'Trait',
    'SecurityScheme',
    'SecuritySchemeSettings',
    'AnnotationType',
    'Library',
    'Overlay',
    'Extension',
]


class ShapeBase(TypedDict):
    """Fields shared by every expanded type.

    Kind-specific facets live on the TypedDicts below. `type` is not here:
    a TypedDict subclass may not re-declare a key, so each variant declares
    its own discriminator.

    Numeric bounds use `ExactDecimal`; counts such as `max_items` remain
    numbers because they are bounded by memory rather than numeric precision.
    """

    id: Address | None
    name: str | None
    inherits: NotRequired['list[ShapeNode]']
    custom_facets: NotRequired['dict[str, Json]']
    declared_facets: NotRequired[dict[str, Property]]
    annotations: NotRequired['list[Applied]']
    type_expr: NotRequired[str]
    display_name: NotRequired[str]
    description: NotRequired[str]
    required: NotRequired[bool]
    default: NotRequired['Json']
    example: NotRequired['Example']
    examples: NotRequired['dict[str, Example]']
    enum: NotRequired['list[Json]']
    xml: NotRequired['Json']
    allowed_targets: NotRequired[list[AnnotationTarget]]


class AnyShape(ShapeBase):
    type: Literal['any']


class NilShape(ShapeBase):
    type: Literal['nil', 'null']


class BooleanShape(ShapeBase):
    type: Literal['boolean']


class StringShape(ShapeBase):
    type: Literal['string']
    max_length: NotRequired[int]
    min_length: NotRequired[int]
    pattern: NotRequired[str]


class IntegerShape(ShapeBase):
    type: Literal['integer']
    format: NotRequired[str]
    maximum: NotRequired[ExactDecimal]  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'
    minimum: NotRequired[ExactDecimal]  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'
    multiple_of: NotRequired[ExactDecimal]  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'


class NumberShape(ShapeBase):
    type: Literal['number']
    format: NotRequired[str]
    maximum: NotRequired[ExactDecimal]  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'
    minimum: NotRequired[ExactDecimal]  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'
    multiple_of: NotRequired[ExactDecimal]  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'


class DateTimeShape(ShapeBase):
    type: Literal['datetime']
    format: NotRequired[str]


class DateTimeOnlyShape(ShapeBase):
    type: Literal['datetime-only']


class DateOnlyShape(ShapeBase):
    type: Literal['date-only']


class TimeOnlyShape(ShapeBase):
    type: Literal['time-only']


class FileShape(ShapeBase):
    type: Literal['file']
    file_types: NotRequired[list[str]]
    max_length: NotRequired[int]
    min_length: NotRequired[int]


class ObjectShape(ShapeBase):
    type: Literal['object']
    additional_properties: NotRequired[bool]
    discriminator: NotRequired[str]
    discriminator_value: NotRequired['Json']
    max_properties: NotRequired[int]
    min_properties: NotRequired[int]
    pattern_properties: NotRequired[dict[str, PatternProperty]]
    properties: NotRequired[dict[str, Property]]


class ArrayShape(ShapeBase):
    type: Literal['array']
    items: NotRequired['ShapeNode']
    max_items: NotRequired[int]
    min_items: NotRequired[int]
    unique_items: NotRequired[bool]


class UnionShape(ShapeBase):
    type: Literal['union']
    any_of: NotRequired['list[ShapeNode]']


class JsonShape(ShapeBase):
    type: Literal['json']
    json_schema: NotRequired['Json']
    projection: NotRequired['Shape']


Shape: TypeAlias = (
    AnyShape
    | NilShape
    | BooleanShape
    | StringShape
    | IntegerShape
    | NumberShape
    | DateTimeShape
    | DateTimeOnlyShape
    | DateOnlyShape
    | TimeOnlyShape
    | FileShape
    | ObjectShape
    | ArrayShape
    | UnionShape
    | JsonShape
)


class Recursion(ShapeBase):
    """A type that repeats here. Do not expand it; look `head` up instead.

    Spelled in `type` rather than a key of its own, so a consumer that
    switches on `type` and has not handled it fails loudly.
    """

    type: Literal['recursive']
    head: Ref


class Document(TypedDict):
    format: Literal['fastraml-tree']
    format_version: Literal[1]
    view: Literal['effective']
    base: Address
    entry_point: 'EntryPoint | None'
    types: 'ShapeDeclarationsByFile'
    annotation_types: 'ShapeDeclarationsByFile'
    security_schemes: 'SecuritySchemeDeclarationsByFile'
    endpoints: 'EndpointsByPath'
    annotations: 'list[DocumentAnnotation]'


class EntryPoint(TypedDict):
    kind: FragmentKind
    base_uri_parameters: NotRequired[dict[str, Parameter]]
    documentation: NotRequired[list[DocumentationItem]]
    secured_by: NotRequired['list[SecuredBy]']
    annotations: NotRequired['list[Applied]']
    title: NotRequired[str]
    version: NotRequired[str]
    base_uri: NotRequired[str]
    media_types: NotRequired[list[str]]
    protocols: NotRequired[list[Protocol]]
    usage: NotRequired[str]
    description: NotRequired[str]


class SecurityScheme(TypedDict):
    id: Address | None
    name: str
    type: SecuritySchemeType
    described_by: NotRequired['DescribedBy']
    annotations: NotRequired['list[Applied]']
    display_name: NotRequired[str]
    description: NotRequired[str]
    settings: NotRequired['SecuritySettings']


class DescribedBy(TypedDict):
    query_string: NotRequired['ShapeNode | None']
    responses: NotRequired['ResponsesByStatus']
    headers: NotRequired[dict[str, Parameter]]
    query_parameters: NotRequired[dict[str, Parameter]]


class Endpoint(TypedDict):
    id: Address | None
    operations: 'OperationsByMethod'
    secured_by: 'list[SecuredBy]'
    uri_parameters: NotRequired[dict[str, Parameter]]
    annotations: NotRequired['list[Applied]']
    display_name: NotRequired[str]
    description: NotRequired[str]


class Operation(TypedDict):
    id: Address | None
    responses: 'ResponsesByStatus'
    description: NotRequired[str]
    display_name: NotRequired[str]
    protocols: NotRequired[list[Protocol]]
    secured_by: NotRequired['list[SecuredBy]']
    annotations: NotRequired['list[Applied]']
    query_string: NotRequired['ShapeNode | None']
    bodies: NotRequired['BodiesByMediaType']
    headers: NotRequired[dict[str, Parameter]]
    query_parameters: NotRequired[dict[str, Parameter]]


class Response(TypedDict):
    description: NotRequired[str]
    headers: NotRequired[dict[str, Parameter]]
    bodies: NotRequired['BodiesByMediaType']
    annotations: NotRequired['list[Applied]']


class SecuredBy(TypedDict):
    name: str
    is_null: bool
    bound: bool
    declaration: Address | None
    scopes: list[str] | None


class Applied(TypedDict):
    name: str
    type: Address | None
    value: 'Json'


class DocumentAnnotation(TypedDict):
    name: str
    target: AnnotationTarget
    type: Address | None
    value: 'Json'


class Example(TypedDict):
    value: 'Json'
    annotations: NotRequired['list[Applied]']
    display_name: NotRequired[str]
    description: NotRequired[str]
    strict: NotRequired[bool]


FORMAT: Final = 'fastraml-tree'
FORMAT_VERSION: Final = 1
VIEW: Final = 'effective'


#: Where a shape sits under each record: the key, how many, what the
#: leaf is, and the record named where the leaf is one. Generated, so a
#: facet that starts holding a shape starts being walked.
CHILDREN: Final[dict[str, tuple[tuple[str, str, str, str], ...]]] = {
    'Document': (
        ('entry_point', 'one', 'record', 'EntryPoint'),
        ('types', 'map_of_map', 'shape_node', ''),
        ('annotation_types', 'map_of_map', 'shape_node', ''),
        ('security_schemes', 'map_of_map', 'record', 'SecurityScheme'),
        ('endpoints', 'map', 'record', 'Endpoint'),
    ),
    'EntryPoint': (('base_uri_parameters', 'map', 'record', 'Parameter'),),
    'SecurityScheme': (('described_by', 'one', 'record', 'DescribedBy'),),
    'DescribedBy': (
        ('headers', 'map', 'record', 'Parameter'),
        ('query_parameters', 'map', 'record', 'Parameter'),
        ('query_string', 'one', 'shape_node', ''),
        ('responses', 'map', 'record', 'Response'),
    ),
    'Endpoint': (('operations', 'map', 'record', 'Operation'), ('uri_parameters', 'map', 'record', 'Parameter')),
    'Operation': (
        ('responses', 'map', 'record', 'Response'),
        ('headers', 'map', 'record', 'Parameter'),
        ('query_parameters', 'map', 'record', 'Parameter'),
        ('query_string', 'one', 'shape_node', ''),
        ('bodies', 'map', 'shape_node', ''),
    ),
    'Response': (('headers', 'map', 'record', 'Parameter'), ('bodies', 'map', 'shape_node', '')),
    'ShapeBase': (('inherits', 'list', 'shape_node', ''), ('declared_facets', 'map', 'record', 'Property')),
    'Property': (('type', 'one', 'shape_node', ''),),
    'PatternProperty': (('type', 'one', 'shape_node', ''),),
    'Parameter': (('type', 'one', 'shape_node', ''),),
    'ObjectShape': (
        ('pattern_properties', 'map', 'record', 'PatternProperty'),
        ('properties', 'map', 'record', 'Property'),
    ),
    'ArrayShape': (('items', 'one', 'shape_node', ''),),
    'UnionShape': (('any_of', 'list', 'shape_node', ''),),
    'JsonShape': (('projection', 'one', 'shape', ''),),
}

#: The `type` discriminator to the record whose keys describe it. A `type`
#: absent from here is a recursion marker or a document this file predates,
#: and either way a walk stops.
KINDS: Final[dict[str, str]] = {
    'any': 'AnyShape',
    'nil': 'NilShape',
    'null': 'NilShape',
    'boolean': 'BooleanShape',
    'string': 'StringShape',
    'integer': 'IntegerShape',
    'number': 'NumberShape',
    'datetime': 'DateTimeShape',
    'datetime-only': 'DateTimeOnlyShape',
    'date-only': 'DateOnlyShape',
    'time-only': 'TimeOnlyShape',
    'file': 'FileShape',
    'object': 'ObjectShape',
    'array': 'ArrayShape',
    'union': 'UnionShape',
    'json': 'JsonShape',
}


__all__ = [
    'Address',
    'AnnotationTarget',
    'AnyShape',
    'Applied',
    'ArrayShape',
    'BodiesByMediaType',
    'BooleanShape',
    'DateOnlyShape',
    'DateTimeOnlyShape',
    'DateTimeShape',
    'DeclarationName',
    'DescribedBy',
    'Document',
    'DocumentAnnotation',
    'DocumentationItem',
    'Endpoint',
    'EndpointPath',
    'EndpointsByPath',
    'EntryPoint',
    'ExactDecimal',
    'Example',
    'FileShape',
    'FragmentKind',
    'HttpMethod',
    'IntegerShape',
    'Json',
    'JsonObject',
    'JsonShape',
    'MediaType',
    'NilShape',
    'NumberShape',
    'ObjectShape',
    'Operation',
    'OperationsByMethod',
    'Parameter',
    'ParameterBinding',
    'PatternProperty',
    'Property',
    'Protocol',
    'Ref',
    'Response',
    'ResponsesByStatus',
    'SecuredBy',
    'SecurityScheme',
    'SecuritySchemeDeclarations',
    'SecuritySchemeDeclarationsByFile',
    'SecuritySchemeType',
    'SecuritySetting',
    'SecuritySettings',
    'Shape',
    'ShapeBase',
    'ShapeDeclarations',
    'ShapeDeclarationsByFile',
    'ShapeNode',
    'ShapeType',
    'SourceFile',
    'StatusCode',
    'StringShape',
    'TimeOnlyShape',
    'UnionShape',
]
