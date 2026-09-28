"""The merge: applying a parent's constraints to a child.

docs/07-resolution-and-inheritance.md § 3 to § 5. The invariant across every
rule here is that a subtype may only narrow: a child may tighten a bound its
parent set, never loosen it.

The per-kind rules live here rather than as methods on the kinds. They are
mutually recursive with the driver (merging two objects merges their
like-named properties, which dispatches back to a kind), and the union rules
construct a `UnionShape`, which `base.py` cannot import.

Nothing here flattens a chain. `unwrap` (docs/07 § 4) decides what to merge
into what and calls `inherit` once per edge, or `fold` once for several
parents.
"""

from __future__ import annotations

import operator
from fractions import Fraction
from functools import partial
from typing import TYPE_CHECKING, Any

from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.types.base import TYPE_JSON, TYPE_UNION, BaseShape, PatternProperty, copyable_slots
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
from fastraml.types.values import is_subset

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastraml.datanode import DataNode
    from fastraml.types.base import ScalarFacet, Shape

__all__ = [
    'alias_to',
    'fold',
    'inherit',
]

#: Integer formats by width class: `int` is an alias for `int32` and `long` for
#: `int64`, so those pairs are compatible (docs/05 § 2). A format outside
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
    # eight parameters, or a bare value where the rule compared one.
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
    to one member (docs/07 § 5).
    """
    if source._visiting:  # noqa: SLF001 - unwrap and this module co-own the flag
        # An inheritance chain that loops. Unlike resolution this is not an
        # error here: the caller marks recursion afterwards (docs/07 § 6).
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
    """The three facets that live on the base, not on the kind (docs/07 § 4)."""
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


def _is_subset(target: list[DataNode], source: list[DataNode]) -> bool:
    """A child's enum may only narrow its parent's (docs/10 § 5 equality)."""
    return is_subset((node.raw for node in target), (node.raw for node in source))


# -- union interaction (docs/07 § 5) ------------------------------------------


def _inherit_from_union(target: BaseShape, union: UnionShape) -> BaseShape:
    """Source is a union, target is not.

    Each compatible member is merged into its *own* copy of the target,
    because the survivors are genuinely new shapes; reusing the target would
    corrupt the declared model. The copy shares the target's parents: nothing
    narrows a parent, and a detached copy of one would be a second, frozen
    version of a type unwrap flattens in place. Each survivor's parents name
    the member it took (`_variant_parents`).

    A survivor is anonymous: the target's name, display name and description
    it arrived with are the union's. Only the one survivor that replaces the
    target keeps them.
    """
    survivors: list[BaseShape] = []
    failures = Accumulator()
    for member in union.any_of or ():
        if isinstance(member.shape, AnyShape):
            # One `any` member makes the whole union constrain nothing.
            return target
        if member.type != target.type:
            continue
        candidate = target.clone({parent.id: parent for parent in target.inherits})
        candidate.id = target._raml.next_id()  # noqa: SLF001 - a new shape needs a new identity
        parents = _variant_parents(target, [member])
        if parents is not None:
            candidate.inherits = parents
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
        survivor.name = None
        survivor.display_name = None
        if survivor.description is target.description:
            survivor.description = None
    return target


def _raise_with_details(error: RamlError, failures: Accumulator) -> BaseShape:
    detail = failures.result()
    raise error.append(detail) if detail is not None else error


def _inherit_into_union(target: BaseShape, target_shape: UnionShape, source: BaseShape) -> BaseShape:
    """Target is a union, source is not: every member must accept the source.

    Each member is replaced by a fold of the member and the source, never
    narrowed in place. A member may be an alias, which shares its referent's
    containers (docs/07 § 3), or one the union adopted from its parent by
    reference (`_narrow_union`); narrowing either would give a declared type
    the source's constraints.
    """
    failures = Accumulator()
    members: list[BaseShape] = []
    for member in target_shape.any_of or ():
        variant = _empty_subtype([member, source])
        try:
            variant = inherit(variant, member)
            # `_inherit`, not `inherit`: the guard is already held by the frame
            # that called us, and the members are siblings rather than a
            # descent. Going through `inherit` would see `source._visiting` set
            # and return without merging anything at all.
            variant = _inherit(variant, source)
        except RamlError as err:
            failures.add(err)
        variant.inherits = _variant_parents(target, [member]) or [member, source]
        members.append(variant)
    failures.raise_if_any()
    target_shape.any_of = members
    return target


def _variant_parents(target: BaseShape, taken: list[BaseShape]) -> list[BaseShape] | None:
    """The parents of a variant of `target` built from `taken`, or `None` if none applies.

    Spec § Union Type expands every union in a type's hierarchy, so each
    variant of `type: [HasHome | IsOnFarm, Cat | Dog]` is a subtype of one
    member of each: `[HasHome, Cat]`, `[HasHome, Dog]`, `[IsOnFarm, Cat]` and
    `[IsOnFarm, Dog]`. Each union among the target's parents is replaced by the
    member of it the variant took, in the declared order.

    What was taken is a union's member, or a variant an earlier merge of the
    same parents built, whose own parents already say which member it took.
    That is how unwrap folds several parents (docs/07 § 4): the fold's
    variants name their members, and the declaration's variants take theirs.
    """
    parents = target.inherits
    chosen = list(parents)
    for at, parent in enumerate(parents):
        members = parent.shape.any_of if isinstance(parent.shape, UnionShape) else None
        if not members:
            continue
        for each in taken:
            if any(member is each for member in members):
                chosen[at] = each
                break
            if _descends(each, parents) and each.inherits[at] is not parent:
                chosen[at] = each.inherits[at]
                break
    return chosen if any(map(operator.is_not, chosen, parents)) else None


def _descends(variant: BaseShape, parents: list[BaseShape]) -> bool:
    """Whether `variant`'s parents are `parents`, with some unions replaced by a member."""
    if len(variant.inherits) != len(parents):
        return False
    for mine, parent in zip(variant.inherits, parents, strict=True):
        if mine is parent:
            continue
        members = parent.shape.any_of if isinstance(parent.shape, UnionShape) else None
        if not members or not any(member is mine for member in members):
            return False
    return True


def fold(parents: list[BaseShape]) -> BaseShape:
    """A new shape that is a subtype of every one of `parents`, in order.

    Unwrap merges several parents through this (docs/07 § 4), and a union
    member narrowed by a non-union through it (docs/07 § 5). The result holds
    what it took from a parent by reference, and never narrows that in place
    (`_borrowed`): a like-named property, pattern property or `items` two
    parents both declare is folded in turn.
    """
    folded = _empty_subtype(parents)
    for parent in parents:
        folded = inherit(folded, parent)
    return folded


def _empty_subtype(parents: list[BaseShape]) -> BaseShape:
    """An empty shape of the first parent's kind, to fold the parents into.

    Its containers start empty rather than `None`. Otherwise the first merge
    would take `_narrow_properties`' shortcut, and the first parent's dict
    would become the fold's; the second merge would then add to it in place,
    corrupting the parent for every other subtype of it.
    """
    first = parents[0]
    raml = first._raml  # noqa: SLF001 - the shape's own registry, for a fresh id
    folded = BaseShape(
        id=raml.next_id(),
        raml=raml,
        location=first.location,
        name=first.name,
        key_pos=first.key_pos,
        value_pos=first.value_pos,
        anchor=first.anchor,
    )
    folded.type = first.type
    folded.inherits = list(parents)
    folded._unwrapped = True  # noqa: SLF001 - built from parents P9 has flattened
    # `shape` reaches this module through `jsonschema_`.
    from fastraml.types.shape import KIND_TO_CLASS  # noqa: PLC0415

    kind = KIND_TO_CLASS.get(first.type)
    if kind is ObjectShape:
        folded.shape = ObjectShape(folded, properties={}, pattern_properties={})
    elif kind is not None:
        # Every remaining kind's constructor takes only the base. An array's
        # `items` stays unset: the first parent's is borrowed, and a second
        # parent's is folded with it.
        folded.shape = kind(folded)  # type: ignore[call-arg]
    else:  # pragma: no cover - P7 leaves every reachable shape with a known kind
        raise RamlError.new(
            'cannot merge parents of an unresolved type',
            first.location,
            first.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'type': first.type},
        )
    return folded


def _borrowed(target: BaseShape, source: Shape, held: BaseShape, find: Callable[[Shape], BaseShape | None]) -> bool:
    """Whether `target` holds `held` by reference from a parent other than `source`.

    Only a fold holds a declaration of two parents: a subtype's own
    declaration, merged with its one parent, is narrowed in place.
    """
    return any(
        parent.shape is not None and parent.shape is not source and find(parent.shape) is held
        for parent in target.inherits
    )


# -- per-kind narrowing (docs/07 § 4) -----------------------------------------


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
        # Dispatched by kind name rather than by class: `JsonShape` imports
        # this module for its projection, so this module cannot import it. The
        # rule copies the kind's slots by name.
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
    # (docs/07 § 4).
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
        # Written by the parent, not here (docs/06 § 3).
        mine.items_written = False
    elif theirs.items is not None:
        if _borrowed(target, theirs, mine.items, _items_of):
            mine.items = fold([mine.items, theirs.items])
        else:
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
    _narrow_pattern_properties(target, mine, theirs)


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
        if _borrowed(target, theirs, child.base, partial(_property_of, name)):
            mine.properties[name] = child.with_base(fold([child.base, parent.base]))
        else:
            inherit(child.base, parent.base)


def _items_of(shape: Shape) -> BaseShape | None:
    return shape.items if isinstance(shape, ArrayShape) else None


def _property_of(name: str, shape: Shape) -> BaseShape | None:
    held = shape.properties.get(name) if isinstance(shape, ObjectShape) and shape.properties else None
    return held.base if held is not None else None


def _pattern_property_of(key: str, shape: Shape) -> BaseShape | None:
    held = shape.pattern_properties.get(key) if isinstance(shape, ObjectShape) and shape.pattern_properties else None
    return held.base if held is not None else None


def _narrow_pattern_properties(target: BaseShape, mine: ObjectShape, theirs: ObjectShape) -> None:
    if mine.pattern_properties is None:
        mine.pattern_properties = dict(theirs.pattern_properties) if theirs.pattern_properties is not None else None
        return
    for key, parent in (theirs.pattern_properties or {}).items():
        child = mine.pattern_properties.get(key)
        if child is None:
            mine.pattern_properties[key] = parent
        elif _borrowed(target, theirs, child.base, partial(_pattern_property_of, key)):
            mine.pattern_properties[key] = PatternProperty(pattern=child.pattern, base=fold([child.base, parent.base]))
        else:
            inherit(child.base, parent.base)


def _narrow_union(target: BaseShape, mine: UnionShape, theirs: UnionShape) -> None:
    """Both are unions: every source member needs a compatible target member.

    Each pairing is a fold of the two members, so a pairing that fails leaves
    no partial merge behind, and neither member is narrowed in place. Its
    parents name both, as `_variant_parents` does.
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
            taken = [member, parent]
            try:
                variant = inherit(inherit(_empty_subtype(taken), member), parent)
            except RamlError:
                continue
            variant.inherits = _variant_parents(target, taken) or taken
            survivors.append(variant)
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
    # Every slot the kind declares, via `copyable_slots` as in `clone` and
    # `alias_to`, so a field added to `JsonShape` cannot be missed (a missed
    # `_compiled` would make `as_shape()` return `None` after P9).
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


# -- aliasing (docs/07 § 3) ---------------------------------------------------


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
