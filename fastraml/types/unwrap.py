"""P9 — unwrap: flatten every inheritance chain, then settle what that decided.

docs/07-resolution-and-inheritance.md § 4 and § 6. Opt-in
(`ParseOptions(unwrap=True)`), because the un-flattened model is what a
formatter or a documentation generator wants: flattening is lossy about which
declaration a facet came from.

Two halves run here, in order. `unwrap_shape` merges each declaration with its
parents in place. `finish_unwrap` is the post-pass over the result: one walk
producing two things, both of which need the *settled* graph.

- **Recursion marking** replaces the point where a type cycle closes with a
  `RecursiveShape`, turning the object graph into a DAG plus explicit back-edges.
  Without it a consumer walking the model naively recurses forever.
- **Union dispatch tables** give a union whose members all discriminate the same
  way a `{discriminatorValue: member}` lookup (docs/05 § 6).

The walk carries a collector for the second. Marking is the last writer of
`any_of` and already visits every union, so the table is built from what that
descent has in hand rather than from a traversal of its own.

Everything is driven off `Raml.fragment_typedefs`, the flat per-file index the
decoder filled with every top-level shape. Nested shapes are reached from their
parents, so there is no graph traversal and no visited set at the top level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.types.base import TYPE_RECURSIVE, BaseShape, KindBase, declaration_facets
from fastraml.types.complex_ import (
    ArrayShape,
    ObjectShape,
    RecursiveShape,
    UnionShape,
)
from fastraml.types.inherit import alias_to, inherit
from fastraml.types.shape import KIND_TO_CLASS
from fastraml.types.values import EnumValues

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Any

    from fastraml.registry import Raml
    from fastraml.types.base import DeclarationFacet, Shape
    from fastraml.yamlnode import Node

__all__ = [
    'finish_unwrap',
    'unwrap_shape',
    'unwrap_shapes',
]


class _Walk:
    """The state one unwrap pass carries.

    `done` is what makes the pass idempotent *and* correct in the one case that
    replaces a shape: a target inheriting from a union can collapse to a single
    member (docs/07 § 5), and every index that referenced the original
    has to end up pointing at the replacement. Keyed on `BaseShape.id` — the
    model's own identity, not `id()`, which neither keeps the object alive nor
    stays unique.
    """

    __slots__ = ('done', 'max_depth', 'raml')

    def __init__(self, raml: Raml) -> None:
        self.raml = raml
        # Read once per pass rather than per level: the ceiling is one number
        # for the whole parse (docs/12 § 3), and the guard is on a hot
        # recursive path.
        self.max_depth = raml.max_depth
        self.done: dict[int, BaseShape] = {}


def unwrap_shapes(raml: Raml) -> None:
    """Flatten every declared type, then finish the pass (docs/07 § 4).

    `Raml.shapes` is rebuilt rather than appended to. After flattening, the old
    entries describe a model that no longer exists — a union member may have
    been replaced by a merged copy, and a shape may have been dropped from the
    graph entirely — so keeping them would leave the index describing shapes no
    consumer can reach.
    """
    walk = _Walk(raml)
    raml.shapes = []
    accumulator = Accumulator()

    for location, shapes in raml.fragment_typedefs.items():
        for index, base in enumerate(shapes):
            try:
                shapes[index] = _unwrap(walk, base, 0)
            except RamlError as err:
                accumulator.add(RamlError.wrap('unwrap shape', err, location, base.key_pos, kind=ErrorKind.UNWRAPPING))

    # The name indices hold the same objects, so this is a cheap second pass
    # over `done` rather than more unwrapping — but it is what keeps
    # `types_in()` pointing at the flattened shape in the collapse case.
    for declared in (*raml.fragment_types.values(), *raml.fragment_annotations.values()):
        for name, base in declared.items():
            declared[name] = walk.done.get(base.id, base)

    # P8 bound `defined_by` to the un-flattened declaration. Left alone, P10
    # would validate annotation values against a shape with no inherited
    # constraints on it, and would do so silently (docs/09 § B3).
    for extension in raml.domain_extensions:
        if extension.defined_by is not None:
            extension.defined_by = walk.done.get(extension.defined_by.id, extension.defined_by)

    accumulator.raise_if_any()
    finish_unwrap(raml)
    raml.unwrapped = True


def unwrap_shape(raml: Raml, base: BaseShape) -> BaseShape:
    """Flatten one declaration. **Use the return value** — it may differ."""
    return _unwrap(_Walk(raml), base, 0)


def _unwrap(walk: _Walk, base: BaseShape, depth: int) -> BaseShape:
    """The driver of docs/07 § 4.

    `_unwrapped` is set *before* recursing, which is what stops a type cycle
    from running away; the shape is incomplete while its children are being
    walked, and that is fine because nothing reads it until the walk returns.
    """
    if base.shape is None:
        raise RamlError.new('declaration has no shape', base.location, base.key_pos, kind=ErrorKind.UNWRAPPING)
    if base._unwrapped:  # noqa: SLF001 - this pass is the field's declared owner
        return walk.done.get(base.id, base)
    if depth > walk.max_depth:
        raise RamlError.new(
            'type nesting too deep',
            base.location,
            base.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'limit': walk.max_depth},
        )
    base._unwrapped = True  # noqa: SLF001 - see above

    if base.link is not None:
        _link_to_inherits(base)

    if base.alias is not None:
        # An alias is not a source and is not merged into anything: it is
        # resolved and returned as it stands (docs/07 § 3).
        result = alias_to(base, _unwrap(walk, base.alias, depth + 1))
        walk.done[base.id] = result
        walk.raml.put_shape(result)
        return result

    source = _unwrap_parents(walk, base, depth)
    _unwrap_children(walk, base.shape, depth)
    _unwrap_custom_facet_defs(walk, base, depth)

    result = inherit(base, source) if source is not None else base
    # After the merge, never before: the "both unions" branch adopts the
    # parent's `anyOf`, so a child that merely narrows a union has no members of
    # its own until `inherit` has run (docs/07 § 5).
    _distribute_union_facets(walk, result, depth)
    walk.done[base.id] = result
    walk.raml.put_shape(result)
    return result


def _distribute_union_facets(walk: _Walk, base: BaseShape, depth: int) -> None:
    """A facet written beside `type: A | B` belongs to the members.

    Spec § Union Type: an instance is valid "if and only if it is a valid
    instance of at least one of the super types obtained by expanding all unions
    in that type hierarchy", so the facet constrains each expanded branch. A
    union recognises no facets of its own, and *which member* decides whether
    `minimum` is a built-in facet or a custom one — so the only way to read one
    is to give each member the YAML nodes and let its kind decode them.

    Each member is replaced by a **subtype** of itself rather than modified.
    Two reasons, and both are corruption if ignored: the "both unions" branch of
    the merge adopts the parent's member objects by reference, so decoding in
    place would narrow the parent type for every other subtype of it; and a
    subtype is what makes the member's own `facets:` declarations visible to
    P10, which walks from the parents.
    """
    distributed = _distribute(walk, base, depth)
    if distributed is not None:
        _report_unplaced_enum_values(*distributed)


#: For each `enum` inside a distributed declaration, keyed by the declaring
#: shape's id: the indices of the values at least one member kept.
_Kept = dict[int, set[int]]


def _distribute(walk: _Walk, base: BaseShape, depth: int) -> tuple[Mapping[str, BaseShape], _Kept] | None:
    """Give each member its facets; return what the enum intersection kept.

    Returned rather than reported, because a member that is itself a union
    distributes in turn, and a value its members all reject may still fit one
    of the enclosing union's other members. A member the intersection leaves
    with an empty `enum` is dropped, and what it kept does not count.
    """
    shape = base.shape
    if not isinstance(shape, UnionShape) or not shape.pending_facets:
        return None
    pending, shape.pending_facets = shape.pending_facets, []
    holders, shape.member_declarations = shape.member_declarations, {}  # the setter stores `None`
    if not shape.any_of:
        # No members to distribute to — a union that declared none and inherited
        # none. Keep the facets rather than dropping them, so P10 still reports
        # them as unknown instead of a constraint vanishing silently.
        KindBase.decode_facets(shape, pending)
        return None
    for holder in holders.values():
        # Flattened once, before any member takes a copy.
        if holder.shape is not None:
            _unwrap_children(walk, holder.shape, depth)
    kept: _Kept = {}
    members: list[BaseShape] = []
    for member in shape.any_of:
        merged, member_kept = _narrowed_member(walk, base, member, pending, holders, depth=depth)
        if merged is None:
            continue
        members.append(merged)
        for site, indices in member_kept.items():
            kept.setdefault(site, set()).update(indices)
    shape.any_of = members
    return holders, kept


def _narrowed_member(  # noqa: PLR0913 - the member, and what the union hands it
    walk: _Walk,
    base: BaseShape,
    member: BaseShape,
    pending: list[Node],
    holders: Mapping[str, BaseShape],
    *,
    depth: int,
) -> tuple[BaseShape | None, _Kept]:
    """One member of a union, with the union's facets applied as a subtype.

    Also returns which values of each distributed `enum` this member kept, or
    `None` for the member when the intersection leaves one of them empty.
    """
    narrowed = BaseShape(
        id=walk.raml.next_id(),
        raml=walk.raml,
        location=base.location,
        name=member.name,
        key_pos=base.key_pos,
        value_pos=base.value_pos,
        anchor=base.anchor,
    )
    # The member's own kind class, so no dispatch table is needed and a member
    # that is itself a union stays one.
    narrowed.type = member.type
    # The `Shape` protocol declares no constructor, so the class has to be taken
    # dynamically; every kind's is `(base, **declaration_facets)`.
    kind_class = cast('Any', type(member.shape))
    table = declaration_facets(kind_class)
    built: dict[str, Any] = {}
    rest: list[Node] = []
    kept: _Kept = {}
    for index in range(0, len(pending), 2):
        key, value = pending[index], pending[index + 1]
        holder = holders.get(key.value)
        if holder is not None and key.value in table:
            memo: dict[int, BaseShape] = {}
            built.update(_detached_declarations(walk.raml, holder, table[key.value], memo))
            if not _intersect_enums(holder, member, memo, kept):
                return None, {}
        else:
            # Not a declaration, or one this member's kind does not take: left
            # for the kind to decode, and to report as unknown (docs/07 § 5).
            rest.append(key)
            rest.append(value)
            if holder is not None and not isinstance(member.shape, UnionShape):
                # That is reported already; the enum inside is not blamed too.
                for site, _ in _enum_sites(holder, None):
                    kept.setdefault(site.id, set()).update(range(len(site.enum or ())))
    narrowed.shape = kind_class(narrowed, **built) if member.shape is not None else None
    narrowed.inherits = [member]
    narrowed._unwrapped = True  # noqa: SLF001 - built during P9, from parts P9 has already flattened
    if narrowed.shape is not None:
        narrowed.shape.decode_facets(rest)
    if isinstance(narrowed.shape, UnionShape):
        # A member that is itself a union passes the declarations on, as it
        # passes the YAML pairs on through `pending_facets`.
        narrowed.shape.member_declarations = holders
    merged = inherit(narrowed, member)
    # A member that is itself a union has just stashed the facets in turn, and
    # what its own members kept is what this member kept.
    nested = _distribute(walk, merged, depth + 1)
    if nested is not None:
        if isinstance(merged.shape, UnionShape) and not merged.shape.any_of:
            return None, {}
        for nested_site, indices in nested[1].items():
            kept.setdefault(nested_site, set()).update(indices)
    walk.raml.put_shape(merged)
    return merged, kept


# -- enum beside a union (docs/07 § 5) ----------------------------------------


def _intersect_enums(holder: BaseShape, member: BaseShape, memo: dict[int, BaseShape], kept: _Kept) -> bool:
    """Narrow each distributed `enum` to the values this member admits.

    Spec § Union Type: "every value of that `enum` MUST meet all restrictions
    associated with at least one of the super types". So each member keeps the
    values its own declaration at that place validates, and a value is an error
    only when no member keeps it (`_report_unplaced_enum_values`). Handing
    every member the whole list instead would fail the subset rule of docs/07
    § 4 on every member, and dropping that rule would let a member accept a
    value its own type rejects, because validation stops at enum membership.

    The copy is found through `memo`, which maps each shape of the holder to
    this member's copy of it. Returns `False` when a non-empty `enum` keeps no
    value: the spec gives an empty `enum` no meaning, so the member is dropped
    rather than left holding one (docs/07 § 5).
    """
    for site, counterpart in _enum_sites(holder, member):
        values = site.enum or []
        indices = set(range(len(values)))
        if counterpart is not None:
            indices = {index for index in indices if _admits(counterpart, values[index].raw)}
            if values and not indices:
                return False
            memo[site.id].enum = EnumValues(values[index] for index in sorted(indices))
        kept.setdefault(site.id, set()).update(indices)
    return True


def _admits(declaration: BaseShape, value: Any) -> bool:
    try:
        declaration.validate_at(value, '$')
    except RamlError:
        return False
    return True


def _enum_sites(holder: BaseShape, member: BaseShape | None) -> list[tuple[BaseShape, BaseShape | None]]:
    """Every shape in `holder` that declares an `enum`, beside its counterpart.

    The counterpart is the member's declaration at the same place — the same
    property name, pattern or `items` — or `None` where the member has none.
    Walked with a visited set: recursion is not marked until P9 ends.
    """
    sites: list[tuple[BaseShape, BaseShape | None]] = []
    seen: set[int] = set()
    stack: list[tuple[BaseShape, BaseShape | None]] = [(holder, member)]
    while stack:
        shape, counterpart = stack.pop()
        if shape.id in seen:
            continue
        seen.add(shape.id)
        if shape.enum is not None:
            sites.append((shape, counterpart))
        stack.extend(_paired_children(shape, counterpart))
    return sites


def _paired_children(shape: BaseShape, counterpart: BaseShape | None) -> list[tuple[BaseShape, BaseShape | None]]:
    """`shape`'s declarations beside `counterpart`'s at the same place."""
    mine, theirs = shape.shape, counterpart.shape if counterpart is not None else None
    pairs: list[tuple[BaseShape, BaseShape | None]] = []
    if isinstance(mine, ObjectShape):
        own = theirs.properties or {} if isinstance(theirs, ObjectShape) else {}
        for name, prop in (mine.properties or {}).items():
            match = own.get(name)
            pairs.append((prop.base, match.base if match is not None else None))
        own_patterns = theirs.pattern_properties or {} if isinstance(theirs, ObjectShape) else {}
        for key, pattern in (mine.pattern_properties or {}).items():
            match_pattern = own_patterns.get(key)
            pairs.append((pattern.base, match_pattern.base if match_pattern is not None else None))
    elif isinstance(mine, ArrayShape) and mine.items is not None:
        items = theirs.items if isinstance(theirs, ArrayShape) else None
        pairs.append((mine.items, items))
    return pairs


def _report_unplaced_enum_values(holders: Mapping[str, BaseShape], kept: _Kept) -> None:
    """A distributed `enum` value that no member admits (docs/07 § 5)."""
    accumulator = Accumulator()
    for holder in holders.values():
        for site, _ in _enum_sites(holder, None):
            placed = kept.get(site.id, set())
            for index, value in enumerate(site.enum or ()):
                if index not in placed:
                    accumulator.add(
                        RamlError.new(
                            'enum value matches no member of the union',
                            value.location,
                            value.value_pos,
                            kind=ErrorKind.UNWRAPPING,
                            info={'index': index},
                        )
                    )
    accumulator.raise_if_any()


def _detached_declarations(
    raml: Raml, holder: BaseShape, facet: DeclarationFacet, memo: dict[int, BaseShape]
) -> dict[str, Any]:
    """One member's own copy of a declaration written beside its union.

    Detached because each member's merge narrows the copy in place, and two
    members sharing one would narrow each other. The copied roots — each
    property, each `items` — are new shapes and take new ids, as a union
    member's merged copy does (docs/07 § 5). `memo` is fresh from the caller,
    and afterwards maps each shape of the holder to its copy.
    """
    shape = holder.clone(memo).shape
    built = {field: getattr(shape, field) for field in facet.fields}
    for field, value in built.items():
        if isinstance(value, BaseShape):
            value.id = raml.next_id()
        elif isinstance(value, dict):
            for declaration in value.values():
                declaration.base.id = raml.next_id()
        elif value is None:
            continue
        else:  # pragma: no cover - the three DeclarationFacet shapes are all above
            msg = f'unexpected declaration field: {field}'
            raise TypeError(msg)
    return built


def _link_to_inherits(base: BaseShape) -> None:
    """`type: !include` is not inheritance at parse time, but the linked shape
    is the semantic parent (docs/07 § 1). Rewriting it here rather than
    earlier keeps the indirection visible to anyone who did not ask to unwrap.
    """
    if base.link is not None and base.link.shape is not None:
        base.inherits = [base.link.shape]
    base.link = None


def _unwrap_parents(walk: _Walk, base: BaseShape, depth: int) -> BaseShape | None:
    """The merge source for one declaration: nothing, one parent, or a synthetic."""
    if not base.inherits:
        return None

    # Every parent is flattened first, so the synthetic shape below can inspect
    # what each of them actually declares.
    base.inherits = [_unwrap(walk, parent, depth + 1) for parent in base.inherits]
    if len(base.inherits) == 1:
        return base.inherits[0]

    synthetic = _make_multiple_inheritance_shape(walk, base.inherits)
    for parent in base.inherits:
        synthetic = inherit(synthetic, parent)
    return synthetic


def _make_multiple_inheritance_shape(walk: _Walk, parents: list[BaseShape]) -> BaseShape:
    """An empty shape of the first parent's kind, to fold the parents into.

    This exists to prevent one specific corruption. If the child merged its
    parents directly, the first merge would take the `if target.properties is
    None: target.properties = source.properties` shortcut and alias the first
    parent's dict into the child; the second merge would then mutate that dict
    in place, corrupting the parent for every *other* subtype that inherits
    from it. Pre-initialising the collections to empty forces the merge loop to
    run instead of taking the shortcut (docs/07 § 4).
    """
    first = parents[0]
    synthetic = BaseShape(
        id=walk.raml.next_id(),
        raml=walk.raml,
        location=first.location,
        key_pos=first.key_pos,
        value_pos=first.value_pos,
        anchor=first.anchor,
    )
    synthetic.type = first.type
    synthetic._unwrapped = True  # noqa: SLF001 - built flattened; it has no parents of its own

    kind = KIND_TO_CLASS.get(first.type)
    if kind is ObjectShape:
        synthetic.shape = ObjectShape(synthetic, properties={}, pattern_properties={})
    elif kind is ArrayShape:
        synthetic.shape = ArrayShape(synthetic, items=_synthetic_items(walk, parents))
    elif kind is not None:
        # Every remaining kind's constructor takes only the base; the two that
        # take children are handled above.
        synthetic.shape = kind(synthetic)  # type: ignore[call-arg]
    else:  # pragma: no cover - P7 leaves every reachable shape with a known kind
        raise RamlError.new(
            'cannot merge parents of an unresolved type',
            first.location,
            first.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'type': first.type},
        )
    return synthetic


def _synthetic_items(walk: _Walk, parents: list[BaseShape]) -> BaseShape | None:
    """An array's synthetic needs its own `items`, one level down.

    Built from the first parent that declares one. A self-referential `items`
    (`A.items is A`) is skipped: recursion has not been marked yet, so
    following it would not terminate.
    """
    for parent in parents:
        shape = parent.shape
        if isinstance(shape, ArrayShape) and shape.items is not None and shape.items is not parent:
            return _make_multiple_inheritance_shape(walk, [shape.items])
    return None


def _unwrap_children(walk: _Walk, shape: Shape, depth: int) -> None:
    """`items`, `properties`, `patternProperties`, `anyOf` — each in place."""
    if isinstance(shape, ArrayShape):
        if shape.items is not None:
            shape.items = _unwrap(walk, shape.items, depth + 1)
    elif isinstance(shape, UnionShape):
        if shape.any_of is not None:
            shape.any_of = [_unwrap(walk, member, depth + 1) for member in shape.any_of]
    elif isinstance(shape, ObjectShape) and shape.properties is not None:
        for name, prop in shape.properties.items():
            shape.properties[name] = prop.with_base(_unwrap(walk, prop.base, depth + 1))
    if isinstance(shape, ObjectShape):
        for pattern_prop in (shape.pattern_properties or {}).values():
            pattern_prop.base = _unwrap(walk, pattern_prop.base, depth + 1)


def _unwrap_custom_facet_defs(walk: _Walk, base: BaseShape, depth: int) -> None:
    """A `facets:` declaration is a declaration too, so it is flattened as well.

    Its own facet declarations are then cleared: a facet cannot itself declare
    facets, and leaving them would let a cycle close through a place recursion
    marking deliberately does not look (docs/07 § 6).
    """
    for name, prop in base.custom_facet_defs.items():
        unwrapped = _unwrap(walk, prop.base, depth + 1)
        unwrapped.custom_facet_defs = {}
        base.custom_facet_defs[name] = prop.with_base(unwrapped)


# -- recursion marking (docs/07 § 6) ------------------------------------------


def finish_unwrap(raml: Raml, *, roots: Iterable[BaseShape] | None = None) -> None:
    """The post-pass over a flattened model: mark cycles, settle union dispatch.

    One walk, two results (see the module docstring). On re-entry a cycle does
    *not* error — unlike resolution, where it is a genuine mistake — it yields a
    `RecursiveShape` the caller substitutes into the slot it came from. Unions
    met along the way are collected, and their tables are built after the walk,
    because marking itself writes `any_of`.

    Runs on **both** unwrap paths: `unwrap_shapes` for the whole registry, and
    `_ensure_unwrapped` for the private copy P10 makes when `validate=True`
    without `unwrap=True`. A union that reaches neither has no table and
    validates by linear scan — correct, but slower and with a worse report.

    `roots` narrows the walk to shapes outside `fragment_typedefs`: that private
    copy needs finishing without the registry's own shapes being walked again
    (docs/10 § 1).
    """
    max_depth = raml.max_depth
    unions: list[UnionShape] = []
    if roots is not None:
        for base in roots:
            _finish(raml, base, 0, max_depth, unions)
    else:
        for shapes in raml.fragment_typedefs.values():
            for base in shapes:
                _finish(raml, base, 0, max_depth, unions)
    for union in unions:
        union.build_dispatch()


def _finish(raml: Raml, base: BaseShape, depth: int, max_depth: int, unions: list[UnionShape]) -> BaseShape | None:
    """Return a marker to put in the caller's slot, or `None` to leave it be.

    `unions` is the collector the caller builds dispatch tables off; it is filled
    as a side effect of the descent rather than by a second walk.
    """
    if base._visiting:  # noqa: SLF001 - unwrap and this pass co-own the flag
        return _make_recursive(raml, base)
    if base.alias is not None and base.alias._visiting:  # noqa: SLF001 - see above
        # A bare reference is an alias, so what stands here is a *copy* of the
        # referent rather than the referent itself, and the cycle would
        # otherwise close one level further in with the copy as its head. The
        # cycle a reader means is the one back to the referent, so follow the
        # alias edge `alias_to` left in place and mark against that.
        return _make_recursive(raml, base.alias)
    if depth > max_depth:
        raise RamlError.new(
            'type nesting too deep',
            base.location,
            base.key_pos,
            kind=ErrorKind.UNWRAPPING,
            info={'limit': max_depth},
        )
    base._visiting = True  # noqa: SLF001 - see above
    if base.shape is not None:
        _finish_children(raml, base.shape, depth, max_depth, unions)

    # Cleared *before* the facet declarations, deliberately: a facet declaration
    # may reference the very type that declares it, and that is not a recursion
    # worth marking — facets cannot nest (docs/07 § 6).
    base._visiting = False  # noqa: SLF001 - see above
    for name, prop in base.custom_facet_defs.items():
        marked = _finish(raml, prop.base, depth + 1, max_depth, unions)
        if marked is not None:
            base.custom_facet_defs[name] = prop.with_base(marked)
    return None


def _finish_children(raml: Raml, shape: Shape, depth: int, max_depth: int, unions: list[UnionShape]) -> None:
    """The four slots a marker can be substituted into (docs/07 § 6).

    Also where a union is collected, because this is the one place that already
    knows it is looking at one.
    """
    if isinstance(shape, ArrayShape):
        if shape.items is not None:
            shape.items = _finish(raml, shape.items, depth + 1, max_depth, unions) or shape.items
    elif isinstance(shape, UnionShape):
        # Collected whether or not it has members: `build_dispatch` is what
        # settles `_dispatch` away from "never unwrapped".
        unions.append(shape)
        if shape.any_of is not None:
            shape.any_of = [_finish(raml, member, depth + 1, max_depth, unions) or member for member in shape.any_of]
    elif isinstance(shape, ObjectShape):
        for name, prop in (shape.properties or {}).items():
            marked = _finish(raml, prop.base, depth + 1, max_depth, unions)
            if marked is not None and shape.properties is not None:
                shape.properties[name] = prop.with_base(marked)
        for pattern_prop in (shape.pattern_properties or {}).values():
            marked = _finish(raml, pattern_prop.base, depth + 1, max_depth, unions)
            if marked is not None:
                pattern_prop.base = marked


def _make_recursive(raml: Raml, head: BaseShape) -> BaseShape:
    """The back-edge itself. Validation delegates to `head`, so behaviour is
    unchanged; only the object graph becomes a DAG.
    """
    base = BaseShape(
        id=raml.next_id(),
        raml=raml,
        location=head.location,
        name=head.name,
        key_pos=head.key_pos,
        value_pos=head.value_pos,
        anchor=head.anchor,
    )
    base.type = TYPE_RECURSIVE
    base.description = head.description
    base.annotations = head.annotations
    base.custom_facets = head.custom_facets
    # No facet declarations: the head provides them.
    base.shape = RecursiveShape(base, head)
    base._unwrapped = True  # noqa: SLF001 - a marker is flattened by construction
    raml.put_shape(base)
    return base
