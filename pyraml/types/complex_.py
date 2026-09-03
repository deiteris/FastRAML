"""The three structured kinds, and the three internal ones.

`object`, `array` and `union` hold declarations, so each publishes a
`DECLARATION_FACETS` table naming the facets whose values are declarations.
`make_shape` reads the table off the class, builds those children itself and
passes them to the constructor; nothing here calls back into `shape.py`, which
is what keeps `types/` pointing one way (docs/02-architecture.md section 2).

`JsonShape`, `UnknownShape` and `RecursiveShape` are not names a document may
write. `UnknownShape` is the one Phase 2 produces most: a declaration whose kind
cannot be settled yet keeps its undigested facet list and goes on the worklist
for P7 (docs/07 section 1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from pyraml.datanode import make_data_node
from pyraml.parser.facets import make_bool_facet, make_int_facet, make_string_facet
from pyraml.types.base import ONE_SHAPE, PROPERTIES, SHAPE_LIST, KindBase, PatternProperty, Property
from pyraml.yamlnode import node_error

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyraml.datanode import DataNode
    from pyraml.types.base import (
        BaseShape,
        DeclarationFacet,
        ScalarFacet,
    )
    from pyraml.yamlnode import Node

__all__ = [
    'ArrayShape',
    'ComplexKind',
    'JsonShape',
    'ObjectShape',
    'RecursiveShape',
    'UnionShape',
    'UnknownShape',
]


class ComplexKind(KindBase):
    """A kind whose values are structures rather than single scalars."""

    __slots__ = ()

    def is_scalar(self) -> bool:
        return False


def _clone_properties(properties: dict[str, Property] | None, memo: dict[int, BaseShape]) -> dict[str, Property] | None:
    if properties is None:
        return None
    return {
        name: Property(name=prop.name, base=prop.base.clone(memo), required=prop.required)
        for name, prop in properties.items()
    }


def _clone_pattern_properties(
    properties: dict[str, PatternProperty] | None, memo: dict[int, BaseShape]
) -> dict[str, PatternProperty] | None:
    if properties is None:
        return None
    # The compiled pattern is shared: `re.Pattern` is immutable, and it is one
    # of the three things `copy.deepcopy` would have copied pointlessly.
    return {key: PatternProperty(pattern=prop.pattern, base=prop.base.clone(memo)) for key, prop in properties.items()}


class ObjectShape(ComplexKind):
    """`object`. Its properties arrive built, because only `shape.py` can build
    a declaration.
    """

    __slots__ = (
        'additional_properties',
        'discriminator',
        'discriminator_value',
        'max_properties',
        'min_properties',
        'pattern_properties',
        'properties',
    )

    DECLARATION_FACETS: ClassVar[Mapping[str, DeclarationFacet]] = {'properties': PROPERTIES}

    def __init__(
        self,
        base: BaseShape,
        *,
        properties: dict[str, Property] | None = None,
        pattern_properties: dict[str, PatternProperty] | None = None,
    ) -> None:
        super().__init__(base)
        self.properties = properties
        #: `/regex/` keys found inside `properties:`, in declaration order —
        #: the first pattern that matches wins (docs/05 section 5.1).
        self.pattern_properties = pattern_properties
        self.min_properties: ScalarFacet[int] | None = None
        self.max_properties: ScalarFacet[int] | None = None
        self.additional_properties: ScalarFacet[bool] | None = None
        self.discriminator: ScalarFacet[str] | None = None
        self.discriminator_value: DataNode | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        raml, location = self.base._raml, self.base.location  # noqa: SLF001
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            match key.value:
                case 'minProperties':
                    self.min_properties = make_int_facet(raml, key, value, location)
                case 'maxProperties':
                    self.max_properties = make_int_facet(raml, key, value, location)
                case 'additionalProperties':
                    self.additional_properties = make_bool_facet(raml, key, value, location)
                case 'discriminator':
                    # Whether the named property exists is P10's question: it may
                    # be inherited, and so invisible until unwrap (docs/05 § 9).
                    self.discriminator = make_string_facet(raml, key, value, location)
                case 'discriminatorValue':
                    self.discriminator_value = make_data_node(raml, key, value, location)
                case _:
                    rest += (key, value)
        super().decode_facets(rest)

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> ObjectShape:
        clone = cast('ObjectShape', super().clone(base, memo))
        clone.properties = _clone_properties(self.properties, memo)
        clone.pattern_properties = _clone_pattern_properties(self.pattern_properties, memo)
        return clone


class ArrayShape(ComplexKind):
    """`array`. `items` is one declaration, built before construction."""

    __slots__ = ('items', 'max_items', 'min_items', 'unique_items')

    DECLARATION_FACETS: ClassVar[Mapping[str, DeclarationFacet]] = {'items': ONE_SHAPE}

    def __init__(self, base: BaseShape, *, items: BaseShape | None = None) -> None:
        super().__init__(base)
        self.items = items
        self.min_items: ScalarFacet[int] | None = None
        self.max_items: ScalarFacet[int] | None = None
        self.unique_items: ScalarFacet[bool] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        raml, location = self.base._raml, self.base.location  # noqa: SLF001
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            match key.value:
                case 'minItems':
                    self.min_items = make_int_facet(raml, key, value, location)
                case 'maxItems':
                    self.max_items = make_int_facet(raml, key, value, location)
                case 'uniqueItems':
                    self.unique_items = make_bool_facet(raml, key, value, location)
                case _:
                    rest += (key, value)
        super().decode_facets(rest)

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> ArrayShape:
        clone = cast('ArrayShape', super().clone(base, memo))
        clone.items = self.items.clone(memo) if self.items is not None else None
        return clone


class UnionShape(ComplexKind):
    """`union`. Its members arrive built, one declaration each."""

    __slots__ = ('any_of',)

    DECLARATION_FACETS: ClassVar[Mapping[str, DeclarationFacet]] = {'anyOf': SHAPE_LIST}

    def __init__(self, base: BaseShape, *, any_of: list[BaseShape] | None = None) -> None:
        super().__init__(base)
        self.any_of = any_of

    def decode_facets(self, pairs: list[Node]) -> None:
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            if key.value in ('discriminator', 'discriminatorValue'):
                # The one discriminator rule checked at decode time: a union has
                # no properties, so this can never become valid (docs/05 § 9).
                raise node_error(
                    'discriminator cannot be used with union type',
                    self.base.location,
                    key,
                    info={'facet': key.value},
                )
            rest += (key, value)
        super().decode_facets(rest)

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> UnionShape:
        clone = cast('UnionShape', super().clone(base, memo))
        clone.any_of = None if self.any_of is None else [member.clone(memo) for member in self.any_of]
        return clone


class JsonShape(ComplexKind):
    """A type declared by an external or inline JSON Schema.

    Spec section Using XML and JSON Schemas: such a type "MUST NOT participate
    in type inheritance or specialization". Half of that is enforced here — any
    sibling facet is an error. The wrapper facets the spec does allow
    (`displayName`, `description`, annotations, `example`/`examples`) are common
    facets, so `shape.py` has already taken them and they never arrive here.
    """

    __slots__ = ('_cached_defs', '_cached_shape', 'raw', 'validator')

    def __init__(self, base: BaseShape, raw: str = '') -> None:
        super().__init__(base)
        #: The schema exactly as written.
        self.raw = raw
        # Compilation, and the lazy conversion to a shape, are Phase 8's
        # (docs/10-validation.md section 6).
        self.validator: object | None = None
        self._cached_shape: BaseShape | None = None
        self._cached_defs: dict[str, BaseShape] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        if pairs:
            raise node_error(
                'cannot define facets on a JSON schema type',
                self.base.location,
                pairs[0],
                info={'facet': pairs[0].value},
            )


class UnknownShape(ComplexKind):
    """A declaration whose kind is not settled yet.

    Its facets are kept undigested, because which of them are facets at all
    depends on the kind. P7 resolves the type, builds the real kind and hands it
    this list (docs/07 section 1).
    """

    __slots__ = ('facets', 'from_mapping')

    def __init__(self, base: BaseShape, facets: list[Node] | None = None, *, from_mapping: bool = True) -> None:
        super().__init__(base)
        self.facets: list[Node] = facets if facets is not None else []
        #: Was the declaration a mapping (`Foo: {type: Bar}`) rather than a bare
        #: scalar (`Foo: Bar`)? It is the only thing that tells P7 whether a
        #: reference is inheritance or an alias, so it is recorded rather than
        #: inferred from `facets` being empty — a mapping carrying nothing but
        #: `type:` also leaves this list empty (docs/06 section 3.1).
        self.from_mapping = from_mapping

    def decode_facets(self, pairs: list[Node]) -> None:
        self.facets = pairs


class RecursiveShape(ComplexKind):
    """A back-edge: the point where a type cycle returns to its head.

    Produced by `mark_recursions` in P9, never by decoding (docs/07 § 4).
    """

    __slots__ = ('head',)

    def __init__(self, base: BaseShape, head: BaseShape) -> None:
        super().__init__(base)
        self.head = head

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> RecursiveShape:
        # `head` is a back-edge into the same graph, so it goes through `memo`:
        # cloning it afresh would unroll the cycle the marker exists to close.
        # The generic path cannot be used at all — `__init__` requires a head.
        return RecursiveShape(base, self.head.clone(memo))
