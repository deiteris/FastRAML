"""P7 — resolution: what kind is this, and which declaration does this name mean?

Drains `Raml.unresolved_shapes`. Every entry carries an `UnknownShape`, left
there by Phase 2 because the document alone could not settle its kind: a type
expression, a bare named reference, a `type:` sequence, or a `type: !include`.
Resolution swaps the real kind in **on the same `BaseShape`**, so every
reference already taken to it stays valid (docs/05 section 1).

Doc 07 sections 1-2 write the driver as methods on `RAML`, transcribed from Go.
They are free functions here: `registry.py` imports nothing from `types/` at
runtime, which is what keeps the import graph acyclic without indirection
(docs/02 section 3).

The AST -> shape visitor of doc 06 section 3 lives here too rather than in a
module of its own, because it and `resolve_shape` are mutually recursive: a
reference's target may itself be unresolved. Both ways out of that recursion
are ruled out by docs/02 section 2 — a callback parameter is the shape that
section rejects, and a deferred import is the indirection it exists to prevent.
go-raml splits them only because Go's ANTLR runtime wants a visitor struct.

Nothing here flattens anything. `inherits` and `alias` edges are recorded;
applying them is Phase 4's (docs/07 sections 3-5). A `link` is resolved but
deliberately **not** rewritten to `inherits` — that is the first step of unwrap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.parser.references import UnresolvedReferenceError, cut_last
from pyraml.types.base import (
    TYPE_ARRAY,
    TYPE_COMPOSITE,
    TYPE_NIL,
    TYPE_UNION,
    BaseShape,
    TypeExprRef,
)
from pyraml.types.complex_ import UnknownShape
from pyraml.types.expressions import (
    Array,
    Optional_,
    Primitive,
    Reference,
    Union,
    parse_expression,
)
from pyraml.types.shape import attach_kind

if TYPE_CHECKING:
    from pyraml.parser.fragments import ReferenceResolver
    from pyraml.positions import Position
    from pyraml.registry import Raml
    from pyraml.types.complex_ import ArrayShape, UnionShape
    from pyraml.types.expressions import RdtNode
    from pyraml.yamlnode import Node

__all__ = [
    'resolve_shape',
    'resolve_shapes',
]


def resolve_shapes(raml: Raml) -> None:
    """Drain the worklist (docs/07 section 1).

    The queue is read until empty rather than iterated, because resolving one
    shape can lengthen it: an expression allocates anonymous inner shapes, and
    a kind's declaration facets — an `items:` or `properties:` left undigested
    while the kind was unknown — are only built once the kind is settled, and
    each of those may be unresolved in turn.

    There is no second traversal of the model. The decoder registered exactly
    the shapes that need work; this touches those and nothing else.
    """
    accumulator = Accumulator()
    queue = raml.unresolved_shapes
    while queue:
        base = queue.popleft()
        try:
            resolve_shape(raml, base)
        except RamlError as err:
            accumulator.add(RamlError.wrap('resolve shape', err, base.location, base.key_pos, kind=ErrorKind.RESOLVING))
    accumulator.raise_if_any()


def resolve_shape(raml: Raml, base: BaseShape) -> None:
    """Settle one declaration's kind, in place (docs/07 section 1.1).

    Idempotent and re-entrant: the visitor calls this on a referent that may
    itself still be unknown, resolving it out of queue order, so the queue
    routinely reaches shapes another shape already finished. That is what the
    second check is for, and it is why it comes before the cycle check — a
    shape reached twice by two referrers is free, and only a genuine cycle is
    an error.
    """
    shape = base.shape
    if shape is None:
        raise RamlError.new('declaration has no shape', base.location, base.key_pos, kind=ErrorKind.RESOLVING)
    if not isinstance(shape, UnknownShape):
        return
    if base._visiting:  # noqa: SLF001 - this pass is the field's declared owner
        # `A: B` / `B: A`. A cycle through a *property* is legal and is marked,
        # not rejected, much later (docs/07 section 4).
        raise RamlError.new(
            'cyclic type reference',
            base.location,
            base.key_pos,
            kind=ErrorKind.RESOLVING,
            info={'type': base.type},
        )
    base._visiting = True  # noqa: SLF001 - see above
    try:
        if base.link is not None:
            _resolve_link(raml, base, shape)
        elif base.type == TYPE_COMPOSITE:
            _resolve_multiple_inheritance(raml, base, shape)
        else:
            _build(raml, shape, _parse(raml, base))
    finally:
        base._visiting = False  # noqa: SLF001 - see above


# -- the three non-expression cases ------------------------------------------


def _resolve_link(raml: Raml, base: BaseShape, target: UnknownShape) -> None:
    """`type: !include other.raml` — take the linked declaration's kind.

    `base.link` is left in place. Rewriting it to `inherits` is the first thing
    unwrap does (docs/07 section 2), and doing it here would hide the
    indirection from a consumer that has not asked for a flattened model.
    """
    linked = base.link.shape if base.link is not None else None
    if linked is None:
        raise RamlError.new('linked data type declares no shape', base.location, base.key_pos, kind=ErrorKind.RESOLVING)
    resolve_shape(raml, linked)
    attach_kind(raml, base, linked.type, target.pending_facets, from_mapping=target.from_mapping)


def _resolve_multiple_inheritance(raml: Raml, base: BaseShape, target: UnknownShape) -> None:
    """`type: [Cat, Dog]` — resolve every parent, then take the first one's kind.

    Whether the parents are mutually compatible is not asked here: that needs
    the merge, and belongs to unwrap (docs/07 section 3.3).
    """
    if not base.inherits:
        raise RamlError.new('type must name at least one parent', base.location, base.key_pos, kind=ErrorKind.RESOLVING)
    for parent in base.inherits:
        resolve_shape(raml, parent)
    attach_kind(raml, base, base.inherits[0].type, target.pending_facets, from_mapping=target.from_mapping)


def _parse(raml: Raml, base: BaseShape) -> RdtNode:
    """Parse `base.type` as an expression, rebasing the error onto this file.

    `parse_expression` is memoised on text alone, so it cannot know which file
    it is reading and its diagnostic carries no location (docs/06 section 2.3).
    Supplying both here is what lets one malformed expression written in 500
    places cost one parse and still produce 500 correctly positioned errors.
    """
    try:
        return parse_expression(base.type, raml.expr_cache)
    except RamlError as err:
        info = dict(err.head.info or {})
        raise RamlError.new(
            err.head.message,
            base.location,
            _column(base, cast('int', info.get('column', 0))),
            kind=ErrorKind.RESOLVING,
            info={**info, 'type': base.type},
        ) from err


# -- the AST -> shape visitor (docs/06 section 3) -----------------------------


def _build(raml: Raml, target: UnknownShape, node: RdtNode) -> None:
    """Replace `target` with the kind `node` denotes, on the same `BaseShape`.

    The pending facets travel with the *outermost* shape only. In `string[]`
    with `minItems: 1` beside it, the bound belongs to the array; the item type
    is a separate declaration and must not see it.
    """
    base = target.base
    facets, from_mapping = target.pending_facets, target.from_mapping

    match node:
        case Primitive():
            attach_kind(raml, base, node.name, facets, from_mapping=from_mapping)
            _note(base, node.col, builtin=node.name)

        case Reference():
            _build_reference(raml, base, node, facets, from_mapping=from_mapping)

        case Array():
            items = _anonymous(raml, base)
            _build(raml, items, node.item)
            attach_kind(raml, base, TYPE_ARRAY, facets, from_mapping=from_mapping)
            # KIND_TO_CLASS maps `array` to ArrayShape by construction.
            # An `items:` facet written beside an array expression is overridden
            # by the expression, which is the more specific statement.
            cast('ArrayShape', base.shape).items = items.base

        case Optional_():
            # `T?` is sugar for `T | nil` (docs/06 section 1).
            member = _anonymous(raml, base)
            _build(raml, member, node.inner)
            _attach_union(raml, base, facets, [member.base, _nil(raml, base)], from_mapping=from_mapping)

        case Union():
            members = []
            for item in node.members:
                member = _anonymous(raml, base)
                _build(raml, member, item)
                members.append(member.base)
            _attach_union(raml, base, facets, members, from_mapping=from_mapping)


def _build_reference(raml: Raml, base: BaseShape, node: Reference, facets: list[Node], *, from_mapping: bool) -> None:
    """A name: bind it, take its kind, and record which edge this is.

    The edge is the whole of docs/06 section 3.1. A mapping declaration narrows
    the referent and so *inherits* from it; a bare scalar one is a pure
    reference and so *aliases* it, borrowing its facets wholesale.
    """
    resolver = base.anchor if base.anchor is not None else raml.resolver_at(base.location)
    ref = _lookup(base, node, resolver)
    if ref is base:
        raise RamlError.new(
            'self-referential type',
            base.location,
            _column(base, node.col),
            kind=ErrorKind.RESOLVING,
            info={'type': node.name},
        )
    # The referent may still be unknown; resolving it out of queue order is why
    # `resolve_shape` has to be idempotent.
    resolve_shape(raml, ref)
    attach_kind(raml, base, ref.type, facets, from_mapping=from_mapping)
    if from_mapping:
        base.inherits.append(ref)
    else:
        base.alias = ref
    _note_reference(base, node, ref, resolver)


def _lookup(base: BaseShape, node: Reference, resolver: ReferenceResolver | None) -> BaseShape:
    """Bind one name in the scope the declaration captured (docs/04 section 4)."""
    if resolver is None:
        raise RamlError.new(
            'no scope to resolve a type name in',
            base.location,
            _column(base, node.col),
            kind=ErrorKind.RESOLVING,
            info={'type': node.name},
        )
    try:
        if base.is_annotation_type:
            return resolver.reference_annotation_type(node.name)
        return resolver.reference_type(node.name)
    except UnresolvedReferenceError as err:
        raise RamlError.new(
            err.reason,
            base.location,
            _column(base, node.col),
            kind=ErrorKind.RESOLVING,
            info={'type': node.name, 'missing': err.name},
        ) from err


def _attach_union(
    raml: Raml, base: BaseShape, facets: list[Node], members: list[BaseShape], *, from_mapping: bool
) -> None:
    attach_kind(raml, base, TYPE_UNION, facets, from_mapping=from_mapping)
    # KIND_TO_CLASS maps `union` to UnionShape by construction.
    cast('UnionShape', base.shape).any_of = members


# -- anonymous inner shapes ---------------------------------------------------


def _anonymous_base(raml: Raml, template: BaseShape) -> BaseShape:
    """A fresh `BaseShape` for an inner type the expression implies.

    `string[]` is two declarations, not one. If the array and its item type
    shared a base, facets written beside the expression would leak onto the
    item type. Exactly two fields carry over (docs/06 section 3): `anchor`, so
    an inner reference resolves in the right namespace, and `type_expr`, so its
    column rebases onto the right scalar.
    """
    base = BaseShape(
        id=raml.next_id(),
        raml=raml,
        location=template.location,
        key_pos=template.key_pos,
        value_pos=template.value_pos,
        anchor=template.anchor,
    )
    base.type_expr = template.type_expr
    raml.put_shape(base)
    return base


def _anonymous(raml: Raml, template: BaseShape) -> UnknownShape:
    """A placeholder for an inner type, ready for `_build` to replace.

    `from_mapping=False`: an inner reference has no facets of its own to narrow
    with, so `Ref[]` makes its item an alias of `Ref` rather than a subtype.
    It is never enqueued — the caller resolves it before returning.
    """
    base = _anonymous_base(raml, template)
    shape = UnknownShape(base, from_mapping=False)
    base.shape = shape
    return shape


def _nil(raml: Raml, template: BaseShape) -> BaseShape:
    """The `nil` member that `?` desugars to."""
    base = _anonymous_base(raml, template)
    attach_kind(raml, base, TYPE_NIL, [], from_mapping=False)
    return base


# -- positions and tooling references (docs/06 sections 2.2 and 3.2) ----------


def _column(base: BaseShape, offset: int) -> Position:
    """A file position for a 0-based offset inside the type expression.

    Exact for a plain scalar, which is how a type expression is all but always
    written. A quoted or block scalar shifts the text right of the position the
    composer reports, and the offset is not adjusted for it; go-raml has the
    same limitation.
    """
    if base.type_expr is None:
        return base.key_pos
    return base.type_expr.position.shifted(offset)


def _note(base: BaseShape, col: int, *, builtin: str) -> None:
    """Record a primitive keyword, so hover can show its documentation."""
    if base.type_expr is None:
        return
    position = _column(base, col)
    base.type_expr_refs.append(TypeExprRef(line=position.line, column=position.column, builtin=builtin))


def _note_reference(base: BaseShape, node: Reference, ref: BaseShape, resolver: ReferenceResolver | None) -> None:
    """Record a type name, so go-to-definition lands on the declaration.

    `lib.Type` emits two: the prefix navigates to the library file, the name to
    the declaration inside it (docs/06 section 3.2).
    """
    if base.type_expr is None:
        return
    position = _column(base, node.col)
    prefix, _name, dotted = cut_last(node.name, '.')
    if not dotted:
        base.type_expr_refs.append(TypeExprRef(line=position.line, column=position.column, resolved=ref))
        return
    link = resolver.library_link(prefix) if resolver is not None else None
    if link is not None:
        base.type_expr_refs.append(
            TypeExprRef(
                line=position.line,
                column=position.column,
                library_link=link,
                library_alias=prefix,
            )
        )
    # Past the prefix and the dot it is written with.
    base.type_expr_refs.append(TypeExprRef(line=position.line, column=position.column + len(prefix) + 1, resolved=ref))
