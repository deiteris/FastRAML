"""How a finding names the type it is about.

One spelling for every rule, so an anonymous member of a union reads the same
in each finding that reports it (docs/18 § 2.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastraml.types.complex_ import UnionShape

if TYPE_CHECKING:
    from fastraml.types.base import BaseShape
    from fastraml.views.lint.engine import Context

__all__ = ['ANONYMOUS', 'label_in', 'type_label']

#: The label of a type with no name of its own and no named holder around it.
ANONYMOUS: Final = 'anonymous'


def _declaring(union: BaseShape, member: BaseShape) -> BaseShape:
    """The union that declares `member`, climbing from `union`.

    An alias shares its referent's members (docs/07 § 3), and a union subtype
    such as `Sub: {type: Input, description: d}` holds `Input`'s member objects
    too. Either way the climb goes to the shape that holds the very same member.
    """
    visited = [union]
    while True:
        for parent in (union.alias, *union.inherits):
            if (
                parent is not None
                and isinstance(parent.shape, UnionShape)
                and any(held is member for held in parent.shape.any_of or ())
                and not any(parent is seen for seen in visited)
            ):
                union = parent
                visited.append(parent)
                break
        else:
            return union


def label_in(base: BaseShape, union: BaseShape | None, union_label: str) -> str:
    """`base`'s label, given the union holding it and that union's own label.

    A type with a name is labelled by it. An anonymous member of a union, such
    as `string` in `Input: string | integer`, is labelled by the declaration,
    property or body that holds the declaring union: `Input` here, `p` for a
    property `p: string | integer`, the media type for a body written inline.
    An anonymous union inside another passes its holder's label through.
    """
    if base.name:
        return base.name
    if union is None:
        return ANONYMOUS
    return _declaring(union, base).name or union_label or ANONYMOUS


def type_label(ctx: Context, iri: str, base: BaseShape) -> str:
    """`label_in` for the type at graph node `iri`, its union read off the `anyOf` edge."""
    if base.name:
        return base.name
    parent = next(iter(ctx.graph.into(iri, ('anyOf',))), None)
    union = None if parent is None else ctx.graph.shape_at(parent.subject)
    if parent is None or union is None:
        return ANONYMOUS
    return label_in(base, union, type_label(ctx, parent.subject, union))
