"""The shape model's foundations.

A RAML type declaration is one `BaseShape` holding one kind-specific `Shape`
object. The split is load-bearing rather than tidy: the kind of a declaration is
unknown when the object is created (`type: Foo` cannot be classified until `Foo`
resolves), and it can change again during resolution, so the kind object has to
be replaceable without invalidating any reference already taken to the
`BaseShape`. See docs/05-type-model.md section 1.

`ScalarFacet` lives here too, because every facet of every shape is one, and a
class that `types/` holds cannot live under `parser/` without inverting the
layering.

Nothing here has a runtime dependency on `pyraml.parser`: those names appear in
annotations only, which `from __future__ import annotations` keeps as strings.
Building a `ScalarFacet` from YAML does need the parser — an include has to be
read, the annotated-scalar form unwrapped — so the builder stays in
`pyraml.parser.facets`. See docs/02-architecture.md section 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from pyraml.positions import UNKNOWN, Position

if TYPE_CHECKING:
    import re
    from collections.abc import Mapping

    from pyraml.datanode import DataNode
    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.fragments import DataTypeFragment, ReferenceResolver
    from pyraml.parser.includes import IncludeInfo
    from pyraml.registry import Raml
    from pyraml.types.examples import Example, Examples
    from pyraml.types.xml import XmlSerialization
    from pyraml.yamlnode import Node

    # Phase 3 replaces this alias with the real class (docs/06 section 5). It is
    # written out so the field list below reads as its finished form.
    TypeExprRef = Any

__all__ = [
    'ONE_SHAPE',
    'PROPERTIES',
    'SHAPE_LIST',
    'BaseShape',
    'DeclarationFacet',
    'PatternProperty',
    'Property',
    'ScalarFacet',
    'Shape',
    'declaration_facets',
]


@dataclass(slots=True, eq=False)
class ScalarFacet[T]:
    """One scalar-valued facet: its value, where it came from, its annotations.

    A facet is never a bare Python value, so `minLength must be <= maxLength`
    can point at the exact line that declared each. The companion rule
    (docs/05 section 2): a facet holding a single scalar is a `ScalarFacet[T]`,
    one holding arbitrary user data is a `DataNode`.
    """

    value: T
    location: str
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: Set when the value arrived through `!include`.
    include: IncludeInfo | None = None
    #: Annotations collected from the annotated-scalar form (docs/03 section 7).
    annotations: dict[str, DomainExtension] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f'ScalarFacet({self.value!r})'


class BaseShape:
    """The half of a declaration that every kind has in common.

    Created before the kind is known, so `shape` starts as `None` and is filled
    by `make_shape` in the same breath — with an `UnknownShape` when the kind
    cannot be settled yet. `id` is unique within one parse, and is what the
    clone operations key their memo on (docs/07 section 5).
    """

    __slots__ = (  # noqa: RUF023 - grouped by role, as in docs/05 section 1
        'id',
        'name',
        'type',
        'shape',
        # common facets (spec section Type Declarations)
        'display_name',
        'description',
        'default',
        'required',
        'example',
        'examples',
        'enum',
        'xml',
        # structure
        'inherits',
        'alias',
        'link',
        'custom_facets',
        'custom_facet_defs',
        'annotations',
        # provenance / tooling
        'type_expr',
        'type_expr_refs',
        'is_annotation_type',
        'anchor',
        'location',
        'key_pos',
        'value_pos',
        # state
        '_unwrapped',
        '_visiting',
        '_raml',
    )

    def __init__(  # noqa: PLR0913 - a model constructor names its fields
        self,
        *,
        id: int,  # noqa: A002 - the field is named `id` in docs/05 section 1
        raml: Raml,
        location: str,
        name: str | None = None,
        key_pos: Position = UNKNOWN,
        value_pos: Position = UNKNOWN,
        anchor: ReferenceResolver | None = None,
        is_annotation_type: bool = False,
    ) -> None:
        self.id = id
        self.name = name
        #: The kind name once known: 'string', 'object', … Empty until then.
        self.type: str = ''
        #: The kind-specific half. Attached by `make_shape`; swapped by P7.
        self.shape: Shape | None = None

        self.display_name: ScalarFacet[str] | None = None
        self.description: ScalarFacet[str] | None = None
        self.default: DataNode | None = None
        self.required: ScalarFacet[bool] | None = None
        self.example: Example | None = None
        self.examples: Examples | None = None
        self.enum: list[DataNode] | None = None
        self.xml: XmlSerialization | None = None

        # The containers are allocated eagerly: an empty dict costs less than a
        # `None` check at every read across four passes.
        self.inherits: list[BaseShape] = []
        self.alias: BaseShape | None = None
        self.link: DataTypeFragment | None = None
        self.custom_facets: dict[str, DataNode] = {}
        self.custom_facet_defs: dict[str, Property] = {}
        self.annotations: dict[str, DomainExtension] = {}

        #: The type expression exactly as written, so P7 can report a column
        #: inside it and tooling can offer go-to-definition on each name.
        self.type_expr: Node | None = None
        self.type_expr_refs: list[TypeExprRef] = []
        self.is_annotation_type = is_annotation_type
        #: The scope unqualified names in this declaration resolve in.
        self.anchor = anchor
        self.location = location
        self.key_pos = key_pos
        self.value_pos = value_pos

        # Owned by later passes: unwrap (P9) and the recursion marker.
        self._unwrapped = False
        self._visiting = False
        self._raml = raml

    def __repr__(self) -> str:
        return f'BaseShape(id={self.id}, name={self.name!r}, type={self.type!r})'


@dataclass(slots=True, eq=False)
class Property:
    """A named property, header, query parameter, URI parameter or facet
    declaration — all six share one syntax (docs/05 section 5).
    """

    name: str
    base: BaseShape
    required: bool

    def __repr__(self) -> str:
        return f'Property({self.name!r}, required={self.required})'


@dataclass(slots=True, eq=False)
class PatternProperty:
    """A `/regex/` key found inside `properties:`.

    Always optional by definition, and matched in declaration order — the first
    pattern that matches wins (docs/05 section 5.1).
    """

    pattern: re.Pattern[str]
    base: BaseShape

    def __repr__(self) -> str:
        return f'PatternProperty({self.pattern.pattern!r})'


@dataclass(frozen=True, slots=True)
class DeclarationFacet:
    """A facet whose value is one or more declarations rather than data.

    A kind that has any publishes them in a class-level `DECLARATION_FACETS`
    table. `make_shape` reads the table off the class it is about to construct,
    builds the children itself, and passes them in under `fields`. The kind
    never calls back into `shape.py`, which is what keeps `types/` pointing one
    way (docs/02-architecture.md section 2).
    """

    #: How to read the facet's value node.
    kind: Literal['shape', 'shape_list', 'properties']
    #: The constructor keywords the built children arrive under. `properties:`
    #: fills two, because `/regex/` keys inside it are routed to pattern
    #: properties (docs/05 section 5.1).
    fields: tuple[str, ...]


#: The three declaration facets in the language, named once so that a kind's
#: table and `make_shape`'s dispatch cannot drift apart.
ONE_SHAPE = DeclarationFacet('shape', ('items',))
SHAPE_LIST = DeclarationFacet('shape_list', ('any_of',))
PROPERTIES = DeclarationFacet('properties', ('properties', 'pattern_properties'))

_NO_DECLARATION_FACETS: Mapping[str, DeclarationFacet] = {}


def declaration_facets(kind: type[Shape]) -> Mapping[str, DeclarationFacet]:
    """The declaration-holding facets of a kind; empty for the fourteen without.

    Read through a helper rather than declared on every `Shape`, so the kinds
    that hold no declarations carry nothing they do not use.
    """
    return getattr(kind, 'DECLARATION_FACETS', _NO_DECLARATION_FACETS)


class Shape(Protocol):
    """The kind-specific half of a declaration.

    In Phase 2 only `decode_facets` has a body on the concrete kinds; the rest
    arrive with Phases 4 and 8 (docs/07, docs/10).
    """

    #: Defined only by the kinds that hold declarations. Read it through
    #: `declaration_facets`, never directly.
    DECLARATION_FACETS: ClassVar[Mapping[str, DeclarationFacet]]

    base: BaseShape

    def decode_facets(self, pairs: list[Node]) -> None:
        """Read the leftover facet list, flat as `[k0, v0, k1, v1, …]`.

        Declaration-valued facets have already been removed and handed to the
        constructor, so everything arriving here is data. Anything the kind does
        not recognise becomes a custom facet value on `base.custom_facets`.
        """
        ...

    def inherit(self, source: Shape) -> Shape: ...

    def alias_to(self, source: Shape) -> Shape: ...

    def check(self) -> None:
        """Is the declaration self-consistent? (P8)"""
        ...

    def validate(self, value: Any, path: str) -> None:
        """Does a data value conform to this declaration? (P10)"""
        ...

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> Shape: ...

    def is_scalar(self) -> bool: ...
