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
