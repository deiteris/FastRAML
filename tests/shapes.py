"""Walks over the shapes a parse left behind, shared by corpus-wide checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastraml.registry import Raml
from fastraml.types.base import EMPTY_DICT, EMPTY_LIST, BaseShape
from fastraml.types.complex_ import ArrayShape, ObjectShape, UnionShape

if TYPE_CHECKING:
    from collections.abc import Iterable


def is_shared_empty(value: object) -> bool:
    """Whether `value` is `EMPTY_LIST` or `EMPTY_DICT` itself, not merely empty."""
    return value is EMPTY_LIST or value is EMPTY_DICT


def _containers() -> tuple[str, ...]:
    fresh = BaseShape(id=0, raml=Raml(), location='file:///')
    found = tuple(name for name in BaseShape.__slots__ if is_shared_empty(getattr(fresh, name, None)))
    assert found, 'a fresh BaseShape holds no shared empty: every loop over CONTAINERS would pass vacuously'
    return found


#: The `BaseShape` containers that start as a shared empty (docs/05 § 1),
#: read off a fresh shape so a new one is covered without editing this list.
CONTAINERS = _containers()


def reachable(roots: Iterable[BaseShape]) -> dict[int, BaseShape]:
    """Every shape reachable from `roots`, keyed by `id`, by explicit stack.

    Recursion would not survive a self-referential type — `Node.next: Node` is
    legal and produces a cycle in the object graph until P9 marks it.
    """
    stack = list(roots)
    seen: dict[int, BaseShape] = {}
    while stack:
        base = stack.pop()
        if id(base) in seen:
            continue
        seen[id(base)] = base

        stack += base.inherits
        stack += [prop.base for prop in base.custom_facet_defs.values()]
        if base.alias is not None:
            stack.append(base.alias)
        if base.link is not None and base.link.shape is not None:
            stack.append(base.link.shape)

        shape = base.shape
        if isinstance(shape, ArrayShape) and shape.items is not None:
            stack.append(shape.items)
        elif isinstance(shape, UnionShape) and shape.any_of is not None:
            stack += shape.any_of
        elif isinstance(shape, ObjectShape):
            stack += [prop.base for prop in (shape.properties or {}).values()]
            stack += [prop.base for prop in (shape.pattern_properties or {}).values()]
    return seen


def unshared_empties(raml: Raml) -> list[str]:
    """Each empty container a shape holds that is not the shared one.

    One is an allocation a write site made without `owned`, or a copy of an
    empty container rather than the shared one kept.
    """
    return [
        f'shape {base.id} ({base.name!r}).{field}'
        for base in reachable(raml.shapes).values()
        for field in CONTAINERS
        if not (value := getattr(base, field)) and not is_shared_empty(value)
    ]
