"""The three structured kinds, and the three internal ones.

`object`, `array` and `union` hold declarations, so each publishes a
`DECLARATION_FACETS` table naming the facets whose values are declarations.
`make_shape` reads the table off the class, builds those children itself and
passes them to the constructor; nothing here calls back into `shape.py`, which
is what keeps `types/` pointing one way (docs/02-architecture.md section 2).

`UnknownShape` and `RecursiveShape` are not names a document may write.
`UnknownShape` is the one Phase 2 produces most: a declaration whose kind cannot
be settled yet keeps its undigested facet list and goes on the worklist for P7
(docs/07 section 1).

**`pending_facets` — one pattern, two users.** `make_shape` hands every kind the
flat `[k0, v0, …]` list its declaration carried, and fourteen of them digest it
on the spot. Two cannot, and both keep it as YAML rather than as `custom_facets`,
because digesting into a `DataNode` throws away what a later pass needs to decode
it properly:

- `UnknownShape`, because the kind is unknown, so *which* of these are facets at
  all is unknown. Consumed by P7's `attach_kind`.
- `UnionShape`, because the kind is known and recognises none of them — they
  belong to the members, which are not settled until P9. Consumed by
  `_distribute_union_facets`.

The reference implementation threads the same list (`shapeFacets`) through every
shape and stores it in exactly one place, `UnknownShape.facets`; the union case
is the gap it still has (docs/01 § 3.7).

`JsonShape` is the fourth structured kind and lives in `jsonschema_.py`, which
sits above this module: compiling a schema needs the loader, and the section 6.3
projection builds object, array and union shapes from what it finds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from pyraml.datanode import make_data_node
from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.parser.facets import make_bool_facet, make_int_facet, make_string_facet
from pyraml.types.base import ONE_SHAPE, PROPERTIES, SHAPE_LIST, KindBase, PatternProperty, Property
from pyraml.types.values import check_non_negative, failure, index_path, key_path, type_name, unique_items
from pyraml.yamlnode import node_error

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

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

    def wrong_type(self, value: Any, path: str, expected: str) -> RamlError:
        return failure(
            'invalid type',
            self.base.location,
            self.base.value_pos,
            info={'path': path, 'expected': expected, 'found': type_name(value)},
        )


def _count_bounds(
    base: BaseShape,
    low: ScalarFacet[int] | None,
    high: ScalarFacet[int] | None,
    names: tuple[str, str],
) -> None:
    """`minItems`/`maxItems` and `minProperties`/`maxProperties` (docs/10 § 2).

    Two rules: each bound is non-negative, and together they are satisfiable.
    """
    accumulator = Accumulator()
    for name, facet in zip(names, (low, high), strict=True):
        if facet is not None:
            accumulator.add(check_non_negative(name, facet.value, base.location, facet.value_pos))
    if low is not None and high is not None and low.value > high.value:
        accumulator.add(
            failure(
                f'{names[0]} exceeds {names[1]}',
                base.location,
                low.key_pos,
                info={'min': low.value, 'max': high.value},
            )
        )
    accumulator.raise_if_any()


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
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> ObjectShape:
        clone = cast('ObjectShape', super().clone(base, memo))
        clone.properties = _clone_properties(self.properties, memo)
        clone.pattern_properties = _clone_pattern_properties(self.pattern_properties, memo)
        return clone

    def check(self) -> None:
        accumulator = Accumulator()
        try:
            _count_bounds(self.base, self.min_properties, self.max_properties, ('minProperties', 'maxProperties'))
        except RamlError as err:
            accumulator.add(err)
        forbids_extras = self.additional_properties is not None and not self.additional_properties.value
        if self.pattern_properties and forbids_extras and self.additional_properties is not None:
            accumulator.add(
                failure(
                    'pattern properties conflict with additionalProperties',
                    self.base.location,
                    self.additional_properties.key_pos,
                )
            )
        try:
            self._check_discriminator()
        except RamlError as err:
            accumulator.add(err)
        for prop in (self.properties or {}).values():
            try:
                prop.base.check()
            except RamlError as err:
                accumulator.add(err)
        for pattern in (self.pattern_properties or {}).values():
            try:
                pattern.base.check()
            except RamlError as err:
                accumulator.add(err)
        accumulator.raise_if_any()

    def _check_discriminator(self) -> None:
        """docs/05 section 9. Checked here because the property may be inherited."""
        if self.discriminator is None:
            if self.discriminator_value is not None:
                # A value with nothing to discriminate on says nothing.
                raise failure('discriminatorValue without discriminator', self.base.location, self.base.value_pos)
            return
        name = self.discriminator.value
        if not self.properties:
            raise failure('discriminator requires properties', self.base.location, self.discriminator.value_pos)
        prop = self.properties.get(name)
        if prop is None:
            raise failure(
                'discriminator property not found',
                self.base.location,
                self.discriminator.value_pos,
                info={'property': name},
            )
        if prop.base.shape is None or not prop.base.shape.is_scalar():
            raise failure(
                'discriminator property must be scalar',
                self.base.location,
                self.discriminator.value_pos,
                info={'property': name},
            )
        if self.discriminator_value is not None:
            prop.base.validate_at(self.discriminator_value.raw, f'$.{name}')

    def validate(self, value: Any, path: str) -> None:
        """docs/10 section 5.1's order, which is observable and therefore fixed."""
        if not isinstance(value, dict):
            raise self.wrong_type(value, path, 'object')
        declared = self.properties or {}
        accumulator = Accumulator()

        # 1. Every missing required property, as one message. Reporting them
        #    one at a time makes a half-written document a scrolling exercise.
        missing = [name for name, prop in declared.items() if prop.required and name not in value]
        if missing:
            accumulator.add(
                failure(
                    'missing required properties',
                    self.base.location,
                    self.base.value_pos,
                    info={'path': path, 'properties': missing},
                )
            )

        # 2. Declared properties, in *declaration* order, so the first error a
        #    caller sees matches the order the document reads in.
        for name, prop in declared.items():
            if name in value:
                try:
                    prop.base.validate_at(value[name], key_path(path, name))
                except RamlError as err:
                    accumulator.add(err)

        # 3. Everything the declaration did not name.
        for name, item in value.items():
            if name in declared:
                continue
            try:
                self._validate_extra(name, item, path)
            except RamlError as err:
                accumulator.add(err)

        count = len(value)
        for bound, message, ok in (
            (
                self.min_properties,
                'too few properties',
                count >= (self.min_properties.value if self.min_properties else 0),
            ),
            (
                self.max_properties,
                'too many properties',
                count <= (self.max_properties.value if self.max_properties else count),
            ),
        ):
            if bound is not None and not ok:
                accumulator.add(
                    failure(
                        message,
                        self.base.location,
                        self.base.value_pos,
                        info={'path': path, 'count': count, 'bound': bound.value},
                    )
                )
        accumulator.raise_if_any()

    def _validate_extra(self, name: str, item: Any, path: str) -> None:
        """A key the declaration did not name: a pattern property, or refused."""
        for pattern in (self.pattern_properties or {}).values():
            # Declaration order, first match wins (docs/05 section 5.1).
            if pattern.pattern.search(name) is not None:
                pattern.base.validate_at(item, key_path(path, name))
                return
        if self.pattern_properties:
            # Spec § Property Declarations, in the words of its own example:
            # pattern properties are "restricting the property names of any
            # additional properties", and `//` is how you "force all additional
            # properties to be a string". So declaring any pattern makes the
            # set of them exhaustive — a key matching none is refused whatever
            # `additionalProperties` says (docs/05 section 5.1).
            raise failure(
                'property name matches no pattern property',
                self.base.location,
                self.base.value_pos,
                info={
                    'path': path,
                    'property': name,
                    'patterns': [pattern.pattern.pattern for pattern in self.pattern_properties.values()],
                },
            )
        if self.additional_properties is not None and not self.additional_properties.value:
            raise failure(
                'additional properties are not allowed',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'property': name},
            )


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
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> ArrayShape:
        clone = cast('ArrayShape', super().clone(base, memo))
        clone.items = self.items.clone(memo) if self.items is not None else None
        return clone

    def check(self) -> None:
        accumulator = Accumulator()
        try:
            _count_bounds(self.base, self.min_items, self.max_items, ('minItems', 'maxItems'))
        except RamlError as err:
            accumulator.add(err)
        if self.items is not None:
            try:
                self.items.check()
            except RamlError as err:
                accumulator.add(err)
        accumulator.raise_if_any()

    def validate(self, value: Any, path: str) -> None:
        if not isinstance(value, list):
            raise self.wrong_type(value, path, 'array')
        accumulator = Accumulator()
        count = len(value)
        if self.min_items is not None and count < self.min_items.value:
            accumulator.add(
                failure(
                    'too few items',
                    self.base.location,
                    self.base.value_pos,
                    info={'path': path, 'count': count, 'minItems': self.min_items.value},
                )
            )
        if self.max_items is not None and count > self.max_items.value:
            accumulator.add(
                failure(
                    'too many items',
                    self.base.location,
                    self.base.value_pos,
                    info={'path': path, 'count': count, 'maxItems': self.max_items.value},
                )
            )
        if self.items is not None:
            for index, item in enumerate(value):
                try:
                    self.items.validate_at(item, index_path(path, index))
                except RamlError as err:
                    accumulator.add(err)
        if self.unique_items is not None and self.unique_items.value:
            duplicate = unique_items(value)
            if duplicate is not None:
                accumulator.add(
                    failure(
                        'items are not unique',
                        self.base.location,
                        self.base.value_pos,
                        info={'path': index_path(path, duplicate)},
                    )
                )
        accumulator.raise_if_any()


class UnionShape(ComplexKind):
    """`union`. Its members arrive built, one declaration each."""

    __slots__ = ('any_of', 'pending_facets')

    DECLARATION_FACETS: ClassVar[Mapping[str, DeclarationFacet]] = {'anyOf': SHAPE_LIST}

    def __init__(self, base: BaseShape, *, any_of: list[BaseShape] | None = None) -> None:
        super().__init__(base)
        self.any_of = any_of
        #: The other `pending_facets` (see the module docstring): facets written
        #: beside `type: A | B`. A union recognises none of its own — every one
        #: of them belongs to the *members*, and which member decides whether
        #: `minimum` is a built-in facet or a custom one. P9 distributes them
        #: once `any_of` is settled (docs/07 section 3.4).
        self.pending_facets: list[Node] = []

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
            rest.append(key)
            rest.append(value)
        # Deliberately *not* `super().decode_facets(rest)`: filing these under
        # `custom_facets` would digest them into `DataNode`s, and distributing
        # one to a member means decoding it against that member's kind, which
        # needs the YAML nodes it was written as.
        self.pending_facets = rest

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> UnionShape:
        clone = cast('UnionShape', super().clone(base, memo))
        clone.any_of = None if self.any_of is None else [member.clone(memo) for member in self.any_of]
        return clone

    def check(self) -> None:
        accumulator = Accumulator()
        for member in self.any_of or ():
            try:
                member.check()
            except RamlError as err:
                accumulator.add(err)
        accumulator.raise_if_any()

    def validate(self, value: Any, path: str) -> None:
        """First member that validates wins; if none does, report all of them.

        Reporting only the last member's failure is the unhelpful thing to do
        here — the reader cannot tell which member they meant to satisfy.
        """
        accumulator = Accumulator()
        for member in self.any_of or ():
            try:
                member.validate_at(value, path)
            except RamlError as err:
                accumulator.add(err)
            else:
                return
        message = 'value matches no member of the union'
        info = {'path': path, 'found': type_name(value)}
        combined = accumulator.result()
        if combined is None:
            # No members at all — reachable only for a union that declared none
            # and inherited none (docs/07 section 3.4).
            raise failure(message, self.base.location, self.base.value_pos, info=info)
        raise RamlError.wrap(
            message, combined, self.base.location, self.base.value_pos, kind=ErrorKind.VALIDATING, info=info
        )


class UnknownShape(ComplexKind):
    """A declaration whose kind is not settled yet.

    One of the two kinds that keep `pending_facets` (see the module docstring):
    here because which of these are facets *at all* depends on a kind nobody
    knows yet. P7 resolves the type, builds the real kind and hands it this list
    (docs/07 section 1).
    """

    __slots__ = ('from_mapping', 'pending_facets')

    def __init__(self, base: BaseShape, facets: list[Node] | None = None, *, from_mapping: bool = True) -> None:
        super().__init__(base)
        self.pending_facets: list[Node] = facets if facets is not None else []
        #: Was the declaration a mapping (`Foo: {type: Bar}`) rather than a bare
        #: scalar (`Foo: Bar`)? It is the only thing that tells P7 whether a
        #: reference is inheritance or an alias, so it is recorded rather than
        #: inferred from `pending_facets` being empty — a mapping carrying
        #: nothing but `type:` also leaves this list empty (docs/06 section 3.1).
        self.from_mapping = from_mapping

    def decode_facets(self, pairs: list[Node]) -> None:
        self.pending_facets = pairs

    def check(self) -> None:
        # Always fails. Reaching it means P7 was skipped, and a silent pass here
        # would hide invariant I5 breaking (docs/10 section 2).
        raise failure(
            'type could not be resolved',
            self.base.location,
            self.base.key_pos,
            info={'type': self.base.type or None, 'name': self.base.name},
        )

    def validate(self, value: Any, path: str) -> None:  # noqa: ARG002 - the shape is unusable whatever the value
        self.check()


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

    def check(self) -> None:
        # The head is checked where it is declared. Following the back-edge here
        # is what a cycle makes non-terminating, and it would say nothing new.
        return

    def validate(self, value: Any, path: str) -> None:
        # No visited set: the *data* is finite even though the type is cyclic,
        # so the recursion is bounded by the value's own depth.
        self.head.validate_at(value, path)
