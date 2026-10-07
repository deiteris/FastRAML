"""Custom-facet ancestor traversal and P7 bindings (docs/07 § 2).

Binding is independent of checking a value. P10 consumes the same ancestor
order for its diagnostics; P9 refreshes bindings when it replaces declarations.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from fastraml.datanode import DataNode
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape, Property


def referent(base: BaseShape) -> BaseShape:
    """The declaration an alias names, shared by facet binding and checking."""
    if base.alias is None:
        return base
    seen: set[BaseShape] = set()
    while base.alias is not None and base not in seen:
        seen.add(base)
        base = base.alias
    return base


def _parents(base: BaseShape) -> Sequence[BaseShape]:
    if base.link is not None and base.link.shape is not None:
        return (*base.inherits, base.link.shape)
    return base.inherits


def facet_ancestors(base: BaseShape) -> Iterator[BaseShape]:
    """Parents in breadth-first declaration order, through aliases and links.

    The declaring type is excluded: its facets constrain subtypes (docs/10 § 4).
    An alias is another name for its referent, not a new subtype.
    """
    seen: set[int] = set()
    queue = deque(_parents(referent(base)))
    while queue:
        current = referent(queue.popleft())
        if current.id in seen:
            continue
        seen.add(current.id)
        yield current
        queue.extend(_parents(current))


def bind_custom_facets(raml: Raml) -> None:
    """Record known declarations for supplied DataNodes; report no value errors.

    DataNode identity survives inheritance copies. Distinct declarations remain
    candidates on invalid/ambiguous input; P10 owns duplicate diagnostics.
    """
    bindings: dict[DataNode, list[Property]] = {}
    seen: set[tuple[DataNode, int]] = set()
    for base in raml.shapes:
        if not base.custom_facets:
            continue
        for ancestor in facet_ancestors(base):
            for name, prop in ancestor.custom_facet_defs.items():
                value = base.custom_facets.get(name)
                if value is None:
                    continue
                key = (value, prop.base.id)
                if key not in seen:
                    seen.add(key)
                    bindings.setdefault(value, []).append(prop)
    raml.custom_facet_refs = bindings
