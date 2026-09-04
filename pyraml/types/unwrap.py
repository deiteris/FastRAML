"""P9 — unwrap: flatten every inheritance chain, then mark the cycles.

docs/07-resolution-and-inheritance.md sections 3.1 to 3.3 and section 4. Opt-in
(`ParseOptions(unwrap=True)`), because the un-flattened model is what a
formatter or a documentation generator wants: flattening is lossy about which
declaration a facet came from.

Two things run here, in order. `unwrap_shape` merges each declaration with its
parents in place. `mark_recursions` then replaces the point where a type cycle
closes with a `RecursiveShape`, turning the object graph into a DAG plus
explicit back-edges — without which any consumer walking the model naively
recurses forever.

Everything is driven off `Raml.fragment_typedefs`, the flat per-file index the
decoder filled with every top-level shape. Nested shapes are reached from their
parents, so there is no graph traversal and no visited set at the top level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.types.base import TYPE_RECURSIVE, BaseShape, KindBase, Property
from pyraml.types.complex_ import (
    ArrayShape,
    ObjectShape,
    RecursiveShape,
    UnionShape,
)
from pyraml.types.inherit import alias_to, inherit
from pyraml.types.shape import KIND_TO_CLASS

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

    from pyraml.registry import Raml
    from pyraml.types.base import Shape
    from pyraml.yamlnode import Node

__all__ = [
    'mark_recursions',
    'unwrap_shape',
    'unwrap_shapes',
]


class _Walk:
    """The state one unwrap pass carries.

    `done` is what makes the pass idempotent *and* correct in the one case that
    replaces a shape: a target inheriting from a union can collapse to a single
    member (docs/07 section 3.4), and every index that referenced the original
    has to end up pointing at the replacement. Keyed on `BaseShape.id` — the
    model's own identity, not `id()`, which neither keeps the object alive nor
    stays unique.
    """

    __slots__ = ('done', 'max_depth', 'raml')

    def __init__(self, raml: Raml) -> None:
        self.raml = raml
        # Read once per pass rather than per level: the ceiling is one number
        # for the whole parse (docs/12 section 14), and the guard is on a hot
        # recursive path.
        self.max_depth = raml.max_depth
        self.done: dict[int, BaseShape] = {}


def unwrap_shapes(raml: Raml) -> None:
    """Flatten every declared type, then mark recursion (docs/07 sections 3-4).

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
    # constraints on it, and would do so silently (docs/09 section B4).
    for extension in raml.domain_extensions:
        if extension.defined_by is not None:
            extension.defined_by = walk.done.get(extension.defined_by.id, extension.defined_by)

    accumulator.raise_if_any()
    mark_recursions(raml)
    raml.unwrapped = True


def unwrap_shape(raml: Raml, base: BaseShape) -> BaseShape:
    """Flatten one declaration. **Use the return value** — it may differ."""
    return _unwrap(_Walk(raml), base, 0)


def _unwrap(walk: _Walk, base: BaseShape, depth: int) -> BaseShape:
    """docs/07 section 3.1's driver.

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
        # resolved and returned as it stands (docs/07 section 3.6).
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
    # its own until `inherit` has run (docs/07 section 3.4).
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
    P10, which walks from `inherits[0]`.
    """
    shape = base.shape
    if not isinstance(shape, UnionShape) or not shape.pending_facets:
        return
    pending, shape.pending_facets = shape.pending_facets, []
    if not shape.any_of:
        # No members to distribute to — a union that declared none and inherited
        # none. Keep the facets rather than dropping them, so P10 still reports
        # them as unknown instead of a constraint vanishing silently.
        KindBase.decode_facets(shape, pending)
        return
    shape.any_of = [_narrowed_member(walk, base, member, pending, depth) for member in shape.any_of]


def _narrowed_member(walk: _Walk, base: BaseShape, member: BaseShape, pending: list[Node], depth: int) -> BaseShape:
    """One member of a union, with the union's facets applied as a subtype."""
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
    narrowed.shape = kind_class(narrowed) if member.shape is not None else None
    narrowed.inherits = [member]
    narrowed._unwrapped = True  # noqa: SLF001 - built during P9, from parts P9 has already flattened
    if narrowed.shape is not None:
        narrowed.shape.decode_facets(list(pending))
    merged = inherit(narrowed, member)
    # A member that is itself a union has just stashed the facets in turn.
    _distribute_union_facets(walk, merged, depth + 1)
    walk.raml.put_shape(merged)
    return merged


def _link_to_inherits(base: BaseShape) -> None:
    """`type: !include` is not inheritance at parse time, but the linked shape
    is the semantic parent (docs/07 section 2). Rewriting it here rather than
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
    run instead of taking the shortcut (docs/07 section 3.3).
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
            shape.properties[name] = Property(
                name=prop.name, base=_unwrap(walk, prop.base, depth + 1), required=prop.required
            )
    if isinstance(shape, ObjectShape):
        for pattern_prop in (shape.pattern_properties or {}).values():
            pattern_prop.base = _unwrap(walk, pattern_prop.base, depth + 1)


def _unwrap_custom_facet_defs(walk: _Walk, base: BaseShape, depth: int) -> None:
    """A `facets:` declaration is a declaration too, so it is flattened as well.

    Its own facet declarations are then cleared: a facet cannot itself declare
    facets, and leaving them would let a cycle close through a place recursion
    marking deliberately does not look (docs/07 section 4).
    """
    for name, prop in base.custom_facet_defs.items():
        unwrapped = _unwrap(walk, prop.base, depth + 1)
        unwrapped.custom_facet_defs = {}
        base.custom_facet_defs[name] = Property(name=prop.name, base=unwrapped, required=prop.required)


# -- recursion marking (docs/07 section 4) ------------------------------------


def mark_recursions(raml: Raml, *, roots: Iterable[BaseShape] | None = None) -> None:
    """Close every type cycle with a `RecursiveShape`.

    Runs after unwrap, from the same roots. On re-entry this does *not* error —
    unlike resolution, where a cycle is a genuine mistake — it returns a marker
    the caller substitutes into the slot it came from.

    `roots` narrows the walk to shapes that are not in `fragment_typedefs`:
    validation unwraps a private *copy* of a declaration, and that copy needs
    marking without the registry's own shapes being walked again (docs/10 § 1).
    """
    max_depth = raml.max_depth
    if roots is not None:
        for base in roots:
            _mark(raml, base, 0, max_depth)
        return
    for shapes in raml.fragment_typedefs.values():
        for base in shapes:
            _mark(raml, base, 0, max_depth)


def _mark(raml: Raml, base: BaseShape, depth: int, max_depth: int) -> BaseShape | None:
    """Return a marker to put in the caller's slot, or `None` to leave it be."""
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
        _mark_children(raml, base.shape, depth, max_depth)

    # Cleared *before* the facet declarations, deliberately: a facet declaration
    # may reference the very type that declares it, and that is not a recursion
    # worth marking — facets cannot nest (docs/07 section 4).
    base._visiting = False  # noqa: SLF001 - see above
    for name, prop in base.custom_facet_defs.items():
        marked = _mark(raml, prop.base, depth + 1, max_depth)
        if marked is not None:
            base.custom_facet_defs[name] = Property(name=prop.name, base=marked, required=prop.required)
    return None


def _mark_children(raml: Raml, shape: Shape, depth: int, max_depth: int) -> None:
    """The four slots a marker can be substituted into (docs/07 section 4)."""
    if isinstance(shape, ArrayShape):
        if shape.items is not None:
            shape.items = _mark(raml, shape.items, depth + 1, max_depth) or shape.items
    elif isinstance(shape, UnionShape):
        if shape.any_of is not None:
            shape.any_of = [_mark(raml, member, depth + 1, max_depth) or member for member in shape.any_of]
    elif isinstance(shape, ObjectShape):
        for name, prop in (shape.properties or {}).items():
            marked = _mark(raml, prop.base, depth + 1, max_depth)
            if marked is not None and shape.properties is not None:
                shape.properties[name] = Property(name=prop.name, base=marked, required=prop.required)
        for pattern_prop in (shape.pattern_properties or {}).values():
            marked = _mark(raml, pattern_prop.base, depth + 1, max_depth)
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
