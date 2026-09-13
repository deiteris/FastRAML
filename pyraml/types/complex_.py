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

from typing import TYPE_CHECKING, ClassVar, Final, NamedTuple, cast

from pyraml.datanode import make_data_node
from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.parser.facets import make_bool_facet, make_int_facet, make_string_facet
from pyraml.types.base import (
    ONE_SHAPE,
    PROPERTIES,
    SHAPE_LIST,
    TYPE_INTEGER,
    TYPE_NUMBER,
    KindBase,
    PatternProperty,
    Property,
)
from pyraml.types.values import (
    as_fraction,
    check_non_negative,
    failure,
    index_path,
    key_path,
    type_name,
    unique_items,
)
from pyraml.yamlnode import node_error

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping
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
        declares_discriminator = False
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
                    declares_discriminator = True
                case 'discriminatorValue':
                    self.discriminator_value = make_data_node(raml, key, value, location)
                    declares_discriminator = True
                case _:
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)
        if declares_discriminator:
            raml._discriminator_shapes.append(self.base)  # noqa: SLF001 - consumed by the pre-P9 check

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


#: Sentinel for "the discriminator property is not in this value". A property
#: present and null is a different thing, and `_select` treats both as no tag.
_ABSENT: Final = object()
#: Fewer than this and there is nothing for a discriminator to choose between.
_MIN_MEMBERS: Final = 2

#: What one member of a discriminated union claims: the key a payload's tag must
#: produce to select it, that value as the author spelled it, and the member.
_Claim = tuple['Hashable', str, 'BaseShape']


class _Claims(NamedTuple):
    """What `_discriminated` found: the property, how it keys, and every claim."""

    name: str
    numeric: bool
    claims: list[_Claim]


class _Dispatch(NamedTuple):
    """A union's discriminator, and the member each tag value selects."""

    #: The property every member discriminates on.
    name: str
    #: Whether that property is declared `integer` or `number`, which decides how
    #: a tag is keyed. See `_tag_key`.
    numeric: bool
    #: `_tag_key(value, numeric=numeric)` -> the member that value selects.
    members: dict[Hashable, BaseShape]
    #: The claimed values as their authors spelled them, sorted, for diagnostics.
    known: tuple[str, ...]


def _spell(value: Any) -> str:
    """A discriminator value as a diagnostic should show it."""
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    return 'null' if value is None else str(value)


def _tag_key(value: Any, *, numeric: bool) -> Hashable | None:
    """A discriminator value as the dispatch table keys it, or `None` if not scalar.

    **The key depends on the discriminator property's declared type**, which is
    why `numeric` is passed in rather than inferred from the value. The two cases
    pull in opposite directions and both are measurable:

    - An `integer` property accepts `1`, `1.0`, `'1'` and `'1.0'` for one value
      (docs/10 § 5 — a number-preserving decoder may hand a numeric string
      through). All four must select the member claiming `1`, so a numeric
      property keys through `as_fraction`.
    - A `string` property accepts `'1'` and `'1.0'` as two *different* strings.
      Keying those through `as_fraction` merges them, and a union of two members
      claiming them is then refused as a collision — a false rejection on a valid
      document.

    One table has one key function, so `_discriminated` requires every member to
    agree on `numeric` exactly as it does on the property's name.
    """
    if isinstance(value, dict | list):
        return None
    if value is True or value is False:
        # Before the numeric branch: Python says `bool` is an `int`, and no RAML
        # author means `true` and `1` to select the same type.
        return ('boolean', value)
    if value is None:
        return ('nil', None)
    if numeric:
        number = as_fraction(value)
        if number is not None:
            return ('number', number)
    return (type_name(value), value)


def _declared_name(shape: BaseShape) -> str | None:
    """The name a union member is known by, for keying the dispatch table.

    `type: Cat | Dog` gives members that are *aliases* of `Cat` and `Dog`, and an
    alias carries no name of its own — it shares its referent's containers, so
    the discriminator is visible on it but the name is not (docs/07 § 3.6).

    **One hop reaches it.** `alias_to` points an alias at a *declaration*, and a
    declaration is named. In `type: Moggy | Dog` the first member resolves to
    `Moggy`, whatever `Moggy` is itself declared as — that is the name it was
    written with, and the name `discriminatorValue` defaults from.
    """
    if shape.name is not None:
        return shape.name
    return None if shape.alias is None else shape.alias.name


def _claim(member: BaseShape, shape: ObjectShape, *, numeric: bool) -> _Claim | None:
    """What this member answers to: its `discriminatorValue`, or its own name.

    The default is the type's name (spec § Using Discriminator), so a member with
    no explicit value and no name — an anonymous shape — has nothing to be keyed
    by, and `None` takes the whole union out of dispatch.
    """
    if shape.discriminator_value is None:
        declared = _declared_name(member)
        return None if declared is None else (_tag_key(declared, numeric=numeric), declared, member)
    raw = shape.discriminator_value.raw
    key = _tag_key(raw, numeric=numeric)
    # A non-scalar `discriminatorValue`, which `_check_discriminator` refuses on
    # its own; nothing here can key by it meanwhile.
    return None if key is None else (key, _spell(raw), member)


def _discriminated(members: list[BaseShape] | None) -> _Claims | None:
    """The discriminator this union agrees on, and what each member claims.

    `None` when the union does not discriminate uniformly. Every way to reach it
    means a linear scan is the only correct answer, and none is an error:

    - **fewer than two members** — nothing to choose between;
    - **a member that is not an object carrying a `discriminator`** — `Cat | string`
      cannot be selected by a property;
    - **two discriminator names**, or two *types* under one name — a value would
      have to be looked up under several keys, or under two key functions;
    - **a member with nothing to be keyed by** (see `_claim`).

    The claims come back **in declaration order and undeduplicated**, because the
    two callers want different things from a repeat: `check` reports it, and
    `build_dispatch` declines to build.
    """
    if members is None or len(members) < _MIN_MEMBERS:
        return None
    found: _Claims | None = None
    for member in members:
        shape = member.shape
        if not isinstance(shape, ObjectShape) or shape.discriminator is None:
            return None
        name = shape.discriminator.value
        numeric = _numeric_tag(shape, name)
        if found is None:
            found = _Claims(name, numeric, [])
        elif (name, numeric) != (found.name, found.numeric):
            return None
        claim = _claim(member, shape, numeric=numeric)
        if claim is None:
            return None
        found.claims.append(claim)
    return found


def _numeric_tag(shape: ObjectShape, name: str) -> bool:
    """Is the discriminator property declared `integer` or `number`?

    Read off the property rather than inferred from the values, because the
    declaration is what decides how a tag is keyed (`_tag_key`). A property that
    is absent or carries no type reads as non-numeric, the conservative answer:
    it keys by spelling, so nothing is merged that the author wrote apart.
    """
    prop = (shape.properties or {}).get(name)
    return prop is not None and prop.base.type in (TYPE_INTEGER, TYPE_NUMBER)


def _duplicates(claims: list[_Claim]) -> tuple[str, ...]:
    """The values claimed by more than one member, spelled and sorted."""
    seen: dict[Hashable, str] = {}
    repeated: set[str] = set()
    for key, spelling, _ in claims:
        if key in seen:
            repeated.add(seen[key])
        else:
            seen[key] = spelling
    return tuple(sorted(repeated))


class UnionShape(ComplexKind):
    """`union`. Its members arrive built, one declaration each."""

    __slots__ = ('_dispatch', 'any_of', 'pending_facets')

    DECLARATION_FACETS: ClassVar[Mapping[str, DeclarationFacet]] = {'anyOf': SHAPE_LIST}

    def __init__(self, base: BaseShape, *, any_of: list[BaseShape] | None = None) -> None:
        super().__init__(base)
        self.any_of = any_of
        #: The dispatch table (docs/05 section 9.1), filled by `build_dispatch`
        #: at the end of P9. `None` until then, and `None` afterwards for a union
        #: that does not discriminate: both mean the same thing to `_select`, so
        #: nothing needs to tell them apart.
        self._dispatch: _Dispatch | None = None
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
        # Not copied: the table holds the *members*, and the clone's are new
        # shapes. Carrying it over would dispatch into the original's graph. The
        # clone gets its own when P9 reaches it.
        clone._dispatch = None  # noqa: SLF001 - same class, and __slots__ has no other way
        return clone

    def build_dispatch(self) -> None:
        """Settle the dispatch table. `finish_unwrap` calls this once per shape.

        It belongs at the end of P9 because `finish_unwrap` is the last pass that
        writes `any_of` — it substitutes a `RecursiveShape` for a cycle's head —
        so a table settled any earlier would hold members the union no longer has.

        Built only when the claims are **distinct**. Two members answering to one
        value leave the table unable to say which a payload selects, so the union
        keeps the linear scan and `check` reports the clash.
        """
        found = _discriminated(self.any_of)
        if found is None or _duplicates(found.claims):
            self._dispatch = None
            return
        self._dispatch = _Dispatch(
            found.name,
            found.numeric,
            {key: member for key, _, member in found.claims},
            tuple(sorted(spelling for _, spelling, _ in found.claims)),
        )

    def dispatch(self) -> _Dispatch | None:
        """The dispatch table, or `None` where this union validates by scan."""
        return self._dispatch

    def _select(self, value: Any, path: str) -> BaseShape | None:
        """The member this payload's tag names, or `None` to fall back to a scan.

        Three ways to reach `None`, and each one has a better report waiting in
        the scan than a dispatch failure would be:

        - **no table** — the union does not discriminate (`_discriminated`);
        - **the value is not a mapping** — it cannot carry a tag at all, and every
          member is an object, so the scan's combined `invalid type` is the answer;
        - **the tag is absent, null, or not a scalar** — whether the property was
          required, and what type it had to be, are the *members'* rules, and they
          state them precisely where this could only say the lookup missed.

        A tag that is present and scalar but names nothing raises instead. That is
        the narrowing D12 records: the author said this property identifies the
        type, so a value identifying none of them is wrong even where a member
        would have accepted the payload structurally.
        """
        table = self._dispatch
        if table is None or not isinstance(value, dict):
            return None
        tag = value.get(table.name, _ABSENT)
        if tag is _ABSENT or tag is None:
            return None
        key = _tag_key(tag, numeric=table.numeric)
        if key is None:
            return None
        member = table.members.get(key)
        if member is None:
            raise failure(
                'unknown discriminator value',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'discriminator': table.name, 'value': _spell(tag), 'known': list(table.known)},
            )
        return member

    def check(self) -> None:
        accumulator = Accumulator()
        for member in self.any_of or ():
            try:
                member.check()
            except RamlError as err:
                accumulator.add(err)
        accumulator.add(self._check_distinct_values())
        accumulator.raise_if_any()

    def _check_distinct_values(self) -> RamlError | None:
        """Two members of a discriminated union may not claim one value.

        Spec § Type Declarations on `discriminatorValue`: the value "is unique in
        the hierarchy of the type". Within a union the requirement is also what
        makes the union usable — where two members answer to `cat`, no payload
        can say which it is, and the dispatch table is not built.

        Scoped to the union, not to the hierarchy. After P9 a subtype has no
        `inherits` edge back to the type that declared the discriminator, so the
        hierarchy a value must be unique *in* is no longer walkable from here;
        what is checked is the set of members a document actually wrote together.
        """
        found = _discriminated(self.any_of)
        duplicates = () if found is None else _duplicates(found.claims)
        if found is None or not duplicates:
            return None
        return failure(
            'discriminator value is claimed by more than one member of the union',
            self.base.location,
            self.base.value_pos,
            info={'discriminator': found.name, 'values': list(duplicates), 'members': len(found.claims)},
        )

    def validate(self, value: Any, path: str) -> None:
        """Dispatch on the discriminator where there is one; otherwise try each member.

        A scan reports *every* member's failure rather than the last one's,
        because a reader given one complaint cannot tell which member it was
        meant to satisfy (docs/05 § 9.1 tabulates both paths).
        """
        selected = self._select(value, path)
        if selected is not None:
            # The one member the tag names. Its failures surface as its own
            # rather than under "matches no member", which is what writing a
            # discriminator buys.
            selected.validate_at(value, path)
            return

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

    Produced by `finish_unwrap` in P9, never by decoding (docs/07 § 4).
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
