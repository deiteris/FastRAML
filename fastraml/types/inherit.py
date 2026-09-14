"""The merge: applying a parent's constraints to a child.

docs/07-resolution-and-inheritance.md sections 3.4 to 3.6. The invariant across
every rule here is that **a subtype may only narrow** — a child may tighten a
bound its parent set, never loosen it.

Both halves live in this module rather than as methods on the kinds, which is
where docs/05 section 1's protocol used to declare them. They are mutually
recursive: merging two objects merges their like-named properties, which is a
base-level merge again, which dispatches back to a kind. A method on the kind
would therefore have to reach back into this driver — the shape docs/02
section 2 rejects — and the union rules need to *construct* a `UnionShape`,
which `base.py` cannot import. docs/02 section 2 already listed `inherit.py` as
the home of the per-kind rules; this follows it.

Nothing here flattens a chain. `unwrap` (docs/07 section 3.1) decides what to
merge into what and calls `inherit` once per edge.
"""

from __future__ import annotations

import operator
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.types.base import TYPE_JSON, TYPE_UNION, BaseShape, copyable_slots
from fastraml.types.complex_ import (
    ArrayShape,
    ObjectShape,
    RecursiveShape,
    UnionShape,
    UnknownShape,
)
from fastraml.types.scalars import (
    AnyShape,
    DateTimeShape,
    FileShape,
    IntegerShape,
    NumberShape,
    StringShape,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastraml.datanode import DataNode
    from fastraml.types.base import ScalarFacet, Shape

__all__ = [
    'alias_to',
    'inherit',
]

#: Integer formats by width class: `int` is an alias for `int32` and `long` for
#: `int64`, so those pairs are compatible (docs/05 section 3). A format outside
#: this table is compared by name — go-raml maps it through a Go map whose zero
#: value silently makes any unknown format equal to `int8`.
_INTEGER_WIDTH: dict[str, int] = {
    'int8': 8,
    'int16': 16,
    'int32': 32,
    'int': 32,
    'int64': 64,
    'long': 64,
}


# -- diagnostics --------------------------------------------------------------


def _violation(target: BaseShape, message: str, source: Any, value: Any) -> RamlError:
    """A narrowing rule was broken. The position is the offending facet's own."""
    # `hasattr` and not a type test: what arrives is a `ScalarFacet` of any of
    # eight parameters, or a bare value where the rule compared one. The
    # `isinstance(value, object)` that stood beside this was true of everything.
    position = target.key_pos
    if hasattr(value, 'value_pos'):
        position = value.value_pos
    return RamlError.new(
        message,
        target.location,
        position,
        kind=ErrorKind.UNWRAPPING,
        info={'source': _shown(source), 'target': _shown(value)},
    )


def _shown(facet: Any) -> Any:
    """A facet's value for an `info` dict; the facet itself is not renderable."""
    return getattr(facet, 'value', facet)


# -- the driver ---------------------------------------------------------------


def inherit(target: BaseShape, source: BaseShape) -> BaseShape:
    """Merge `source` into `target` in place, and return what to use.

    **The return value may be a different object**, and callers must use it.
    That happens when a target inherits from a union and the result collapses
    to one member (section 3.4); it is not a quirk to paper over.
    """
    if source._visiting:  # noqa: SLF001 - unwrap and this module co-own the flag
        # An inheritance chain that loops. Unlike resolution this is not an
        # error here: the caller marks recursion afterwards (docs/07 § 4).
        return source
    source._visiting = True  # noqa: SLF001 - see above
    try:
        return _inherit(target, source)
    finally:
        source._visiting = False  # noqa: SLF001 - see above


def _inherit(target: BaseShape, source: BaseShape) -> BaseShape:
    _inherit_base_facets(target, source)

    source_shape, target_shape = source.shape, target.shape
    if source_shape is None or target_shape is None:
        raise RamlError.new('declaration has no shape', target.location, target.key_pos, kind=ErrorKind.UNWRAPPING)

    if isinstance(source_shape, AnyShape):
        # `any` constrains nothing, so there is nothing to narrow with.
        return target

    # Written as two full isinstance pairs rather than hoisted booleans so the
    # type checker can narrow `*_shape` inside each branch.
    if isinstance(source_shape, UnionShape) and not isinstance(target_shape, UnionShape):
        return _inherit_from_union(target, source_shape)
    if isinstance(target_shape, UnionShape) and not isinstance(source_shape, UnionShape):
        return _inherit_into_union(target, target_shape, source)

    _narrow(target, target_shape, source_shape)
    return target


def _inherit_base_facets(target: BaseShape, source: BaseShape) -> None:
    """The three facets that live on the base, not on the kind (docs/07 § 3.5)."""
    if target.description is None:
        target.description = source.description

    for name, value in source.custom_facets.items():
        # Union, target wins per key.
        target.custom_facets.setdefault(name, value)

    if target.enum is None:
        target.enum = source.enum
    elif source.enum is not None and not _is_subset(target.enum, source.enum):
        raise RamlError.new(
            'enum constraint violation',
            target.location,
            target.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'source': _enum_values(source.enum), 'target': _enum_values(target.enum)},
        )


def _enum_values(enum: list[DataNode]) -> list[Any]:
    return [node.raw for node in enum]


def _hashable(value: Any) -> Any:
    """Enum members are scalars in practice; anything else compares by text."""
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _is_subset(target: list[DataNode], source: list[DataNode]) -> bool:
    """A child's enum may only narrow its parent's."""
    allowed = {_hashable(node.raw) for node in source}
    return all(_hashable(node.raw) in allowed for node in target)


# -- union interaction (docs/07 section 3.4) ----------------------------------


def _inherit_from_union(target: BaseShape, source: UnionShape) -> BaseShape:
    """Source is a union, target is not.

    Each compatible member is merged into its *own* detached copy of the
    target, because the survivors are genuinely new shapes; reusing the target
    would corrupt the declared model.
    """
    survivors: list[BaseShape] = []
    failures = Accumulator()
    for member in source.any_of or ():
        if isinstance(member.shape, AnyShape):
            # One `any` member makes the whole union constrain nothing.
            return target
        if member.type != target.type:
            continue
        candidate = target.clone_detached()
        candidate.id = target._raml.next_id()  # noqa: SLF001 - a new shape needs a new identity
        try:
            survivors.append(inherit(candidate, member))
        except RamlError as err:
            failures.add(err)

    if not survivors:
        error = RamlError.new(
            'failed to find compatible union member',
            target.location,
            target.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'target': target.type},
        )
        return _raise_with_details(error, failures)
    if len(survivors) == 1:
        # Simplification: the target *becomes* the one member that survived.
        return survivors[0]

    target.type = TYPE_UNION
    target.shape = UnionShape(target, any_of=survivors)
    for survivor in survivors:
        # Each survivor is a clone of the target, so it arrived carrying the
        # target's own example, examples and default. Those were written about
        # the union as a whole and are validated on it; left on the members they
        # would require every example to satisfy *every* member, which is the
        # opposite of what a union means.
        survivor.example = None
        survivor.examples = None
        survivor.default = None
    return target


def _raise_with_details(error: RamlError, failures: Accumulator) -> BaseShape:
    detail = failures.result()
    raise error.append(detail) if detail is not None else error


def _inherit_into_union(target: BaseShape, target_shape: UnionShape, source: BaseShape) -> BaseShape:
    """Target is a union, source is not: every member must accept the source."""
    failures = Accumulator()
    for member in target_shape.any_of or ():
        try:
            # `_inherit`, not `inherit`: the guard is already held by the frame
            # that called us, and the members are siblings rather than a
            # descent. Going through `inherit` would see `source._visiting` set
            # and return without merging anything at all.
            _inherit(member, source)
        except RamlError as err:
            failures.add(err)
    failures.raise_if_any()
    return target


# -- per-kind narrowing (docs/07 section 3.5) ---------------------------------


def _narrow(target: BaseShape, target_shape: Shape, source_shape: Shape) -> None:
    if isinstance(source_shape, RecursiveShape):
        # A property typed as a back-reference still merges: compare against
        # the type the cycle returns to.
        head = source_shape.head.shape
        if head is not None:
            source_shape = head

    if isinstance(target_shape, UnknownShape) or isinstance(source_shape, UnknownShape):
        raise RamlError.new(
            'cannot inherit from an unresolved type',
            target.location,
            target.key_pos,
            kind=ErrorKind.UNWRAPPING,
        )
    if isinstance(target_shape, RecursiveShape):
        raise RamlError.new(
            'cannot inherit from a recursive type', target.location, target.key_pos, kind=ErrorKind.UNWRAPPING
        )

    if type(target_shape) is not type(source_shape):
        raise RamlError.new(
            'cannot inherit from different type',
            target.location,
            target.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'source': source_shape.base.type, 'target': target_shape.base.type},
        )

    rule = _RULES.get(type(target_shape))
    if rule is not None:
        rule(target, target_shape, source_shape)
    elif target.type == TYPE_JSON:
        # Dispatched by kind name rather than by class, because `JsonShape` sits
        # *above* this module (docs/02 § 3) — it needs the loader and, for the
        # § 6.3 projection, this module. The rule copies the kind's slots by
        # name, so no import is needed to run it.
        _narrow_json(target, target_shape, source_shape)


def _bound(  # noqa: PLR0913, PLR0917 - one narrowing rule needs all six
    target: BaseShape,
    target_shape: Any,
    source_shape: Any,
    field: str,
    message: str,
    breaks: Callable[[Any, Any], bool],
) -> None:
    """One narrowable bound: absent on the target means inherit it outright."""
    mine: ScalarFacet[Any] | None = getattr(target_shape, field)
    theirs: ScalarFacet[Any] | None = getattr(source_shape, field)
    if mine is None:
        setattr(target_shape, field, theirs)
    elif theirs is not None and breaks(mine.value, theirs.value):
        raise _violation(target, message, theirs, mine)


def _not_a_multiple(mine: Fraction, theirs: Fraction) -> bool:
    """The child's `multipleOf` must itself be a multiple of the parent's.

    Otherwise a value the child admits could be one the parent rejects.
    """
    return (Fraction(mine) / Fraction(theirs)).denominator != 1


def _narrow_string(target: BaseShape, mine: StringShape, theirs: StringShape) -> None:
    _bound(target, mine, theirs, 'min_length', 'minLength constraint violation', operator.lt)
    _bound(target, mine, theirs, 'max_length', 'maxLength constraint violation', operator.gt)
    # The child's pattern wins outright; the parent's only fills a gap. Whether
    # two patterns can *both* hold is a question about their languages, and the
    # spec's rule about conflicting patterns is scoped to multiple inheritance
    # (docs/07 § 3.5).
    if mine.pattern is None:
        mine.pattern = theirs.pattern


def _narrow_file(target: BaseShape, mine: FileShape, theirs: FileShape) -> None:
    _bound(target, mine, theirs, 'min_length', 'minLength constraint violation', operator.lt)
    _bound(target, mine, theirs, 'max_length', 'maxLength constraint violation', operator.gt)
    if mine.file_types is None:
        mine.file_types = theirs.file_types
    elif theirs.file_types is not None:
        allowed = {facet.value for facet in theirs.file_types}
        if not {facet.value for facet in mine.file_types} <= allowed:
            raise RamlError.new(
                'fileTypes constraint violation',
                target.location,
                target.key_pos,
                kind=ErrorKind.UNWRAPPING,
                info={
                    'source': sorted(allowed),
                    'target': sorted(facet.value for facet in mine.file_types),
                },
            )


def _narrow_number(target: BaseShape, mine: NumberShape, theirs: NumberShape) -> None:
    _bound(target, mine, theirs, 'minimum', 'minimum constraint violation', operator.lt)
    _bound(target, mine, theirs, 'maximum', 'maximum constraint violation', operator.gt)
    _bound(target, mine, theirs, 'multiple_of', 'multipleOf constraint violation', _not_a_multiple)
    _bound(target, mine, theirs, 'format', 'format constraint violation', operator.ne)


def _narrow_integer(target: BaseShape, mine: IntegerShape, theirs: IntegerShape) -> None:
    _bound(target, mine, theirs, 'minimum', 'minimum constraint violation', operator.lt)
    _bound(target, mine, theirs, 'maximum', 'maximum constraint violation', operator.gt)
    _bound(target, mine, theirs, 'multiple_of', 'multipleOf constraint violation', _not_a_multiple)
    _bound(target, mine, theirs, 'format', 'format constraint violation', _different_width)


def _different_width(mine: str, theirs: str) -> bool:
    """`int` and `int32` name one width, as do `long` and `int64`."""
    if mine in _INTEGER_WIDTH and theirs in _INTEGER_WIDTH:
        return _INTEGER_WIDTH[mine] != _INTEGER_WIDTH[theirs]
    return mine != theirs


def _narrow_datetime(target: BaseShape, mine: DateTimeShape, theirs: DateTimeShape) -> None:
    _bound(target, mine, theirs, 'format', 'format constraint violation', operator.ne)


def _narrow_array(target: BaseShape, mine: ArrayShape, theirs: ArrayShape) -> None:
    if mine.items is None:
        mine.items = theirs.items
    elif theirs.items is not None:
        inherit(mine.items, theirs.items)
    _bound(target, mine, theirs, 'min_items', 'minItems constraint violation', operator.lt)
    _bound(target, mine, theirs, 'max_items', 'maxItems constraint violation', operator.gt)
    if mine.unique_items is None:
        mine.unique_items = theirs.unique_items
    elif theirs.unique_items is not None and theirs.unique_items.value and not mine.unique_items.value:
        raise _violation(target, 'uniqueItems constraint violation', theirs.unique_items, mine.unique_items)


def _narrow_object(target: BaseShape, mine: ObjectShape, theirs: ObjectShape) -> None:
    if mine.additional_properties is None:
        mine.additional_properties = theirs.additional_properties
    if mine.discriminator is None:
        mine.discriminator = theirs.discriminator
    _bound(target, mine, theirs, 'min_properties', 'minProperties constraint violation', operator.lt)
    _bound(target, mine, theirs, 'max_properties', 'maxProperties constraint violation', operator.gt)
    _narrow_properties(target, mine, theirs)
    _narrow_pattern_properties(mine, theirs)


def _narrow_properties(target: BaseShape, mine: ObjectShape, theirs: ObjectShape) -> None:
    if mine.properties is None:
        mine.properties = dict(theirs.properties) if theirs.properties is not None else None
        return
    for name, parent in (theirs.properties or {}).items():
        child = mine.properties.get(name)
        if child is None:
            mine.properties[name] = parent
            continue
        if parent.required and not child.required:
            raise RamlError.new(
                'cannot make a required property optional',
                target.location,
                child.base.key_pos,
                kind=ErrorKind.UNWRAPPING,
                info={'property': name},
            )
        inherit(child.base, parent.base)


def _narrow_pattern_properties(mine: ObjectShape, theirs: ObjectShape) -> None:
    if mine.pattern_properties is None:
        mine.pattern_properties = dict(theirs.pattern_properties) if theirs.pattern_properties is not None else None
        return
    for key, parent in (theirs.pattern_properties or {}).items():
        child = mine.pattern_properties.get(key)
        if child is None:
            mine.pattern_properties[key] = parent
        else:
            inherit(child.base, parent.base)


def _narrow_union(target: BaseShape, mine: UnionShape, theirs: UnionShape) -> None:
    """Both are unions: every source member needs a compatible target member.

    Each pairing is tried on a detached copy, so a member that fails leaves no
    partial merge behind.
    """
    if not mine.any_of:
        # `T: {type: SomeUnion, …}` declares no members of its own, so it takes
        # the parent's outright — the same rule every other facet follows when
        # the child is silent about it.
        mine.any_of = theirs.any_of
        return

    survivors: list[BaseShape] = []
    for parent in theirs.any_of or ():
        matched = False
        for member in mine.any_of or ():
            if member.type != parent.type:
                continue
            candidate = member.clone_detached()
            candidate.id = target._raml.next_id()  # noqa: SLF001 - a new shape needs a new identity
            try:
                survivors.append(inherit(candidate, parent))
            except RamlError:
                continue
            matched = True
        if not matched:
            raise RamlError.new(
                'failed to find compatible union member',
                target.location,
                target.key_pos,
                kind=ErrorKind.UNWRAPPING,
                info={'source': parent.type},
            )
    mine.any_of = survivors


def _narrow_json(target: BaseShape, mine: Any, theirs: Any) -> None:
    if mine.raw and theirs.raw and mine.raw != theirs.raw:
        raise RamlError.new(
            'cannot inherit from a different JSON schema',
            target.location,
            target.value_pos,
            kind=ErrorKind.UNWRAPPING,
        )
    # Every slot the kind declares, not a list written here. The list was
    # `raw` and `validator`; `_compiled` was added to the kind and not to the
    # list, so `as_shape()` returned None on every *declared* schema type once
    # P9 had run — the § 6.3 projection unreachable at exactly the shape a
    # consumer holds. `copyable_slots` is what `clone` and `alias_to` already
    # use so that a field added to a kind cannot be missed, and it needs no
    # import of `JsonShape`, which this module may not have (docs/02 § 3).
    for slot in copyable_slots(type(mine)):
        setattr(mine, slot, getattr(theirs, slot))


#: Kind to its narrowing rule. A kind absent from the table constrains nothing
#: beyond the kind check itself: `boolean`, `nil`, `any` and the three
#: date-only kinds have no facets to narrow.
_RULES: dict[type, Callable[[BaseShape, Any, Any], None]] = {
    StringShape: _narrow_string,
    FileShape: _narrow_file,
    NumberShape: _narrow_number,
    IntegerShape: _narrow_integer,
    DateTimeShape: _narrow_datetime,
    ArrayShape: _narrow_array,
    ObjectShape: _narrow_object,
    UnionShape: _narrow_union,
}


# -- aliasing (docs/07 section 3.6) -------------------------------------------


def alias_to(target: BaseShape, source: BaseShape) -> BaseShape:
    """`target` *is* `source` under a different name.

    Not a subtype: the referent's facets are taken wholesale rather than
    narrowed. The target keeps its own `name`, `id`, `location` and positions —
    that is the entire point of an alias, and what makes it different from
    inheriting with no extra facets.
    """
    target_shape, source_shape = target.shape, source.shape
    if target_shape is None or source_shape is None:
        raise RamlError.new('declaration has no shape', target.location, target.key_pos, kind=ErrorKind.UNWRAPPING)
    if type(target_shape) is not type(source_shape):
        raise RamlError.new(
            'cannot alias a different type',
            target.location,
            target.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'source': source.type, 'target': target.type},
        )

    # Every kind's alias is the same operation — take all of the source's own
    # fields — so it is one slot copy rather than seventeen methods.
    #
    # The values are taken as they stand, containers included: `properties` on
    # the alias *is* `properties` on the referent. That is the point. The alias
    # names one type under a second name, so a later change to the referent has
    # to show through it; copying the dict would let the two drift into two
    # types that a reader believes are one.
    for name in copyable_slots(type(target_shape)):
        setattr(target_shape, name, getattr(source_shape, name))

    target.display_name = source.display_name
    target.description = source.description
    target.example = source.example
    target.examples = source.examples
    target.default = source.default
    target.required = source.required
    target.enum = source.enum
    target.xml = source.xml
    target.inherits = source.inherits
    target.custom_facets = source.custom_facets
    target.custom_facet_defs = source.custom_facet_defs
    target.annotations = source.annotations
    return target
