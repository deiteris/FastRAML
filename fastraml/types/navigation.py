"""Typed-value descent without using validation to guess a union alternative.

Matching uses the same property and discriminator operations as validation
(docs/05 § 4 and § 6). Callers decide how to present ambiguous candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastraml.errors import RamlError
from fastraml.positions import UNKNOWN, Position
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.datanode import ValueNode
    from fastraml.types.base import BaseShape, PatternProperty, Property


@dataclass(frozen=True, slots=True, eq=False)
class TypedChild:
    name: str | int
    value: ValueNode
    base: BaseShape
    declaration: Property | PatternProperty | None = None
    key_pos: Position = UNKNOWN


def branches(base: BaseShape, value: ValueNode) -> Iterator[BaseShape]:
    """All structural candidates, or none for an unknown discriminator tag."""
    pending = [iter((base,))]
    seen: set[BaseShape] = set()
    while pending:
        current = next(pending[-1], None)
        if current is None:
            pending.pop()
            continue
        if current in seen:
            continue
        seen.add(current)
        shape = current.shape
        if isinstance(shape, RecursiveShape):
            pending.append(iter((shape.head,)))
        elif current.alias is not None:
            pending.append(iter((current.alias,)))
        elif isinstance(shape, UnionShape):
            try:
                selected = shape.select(value.raw)
            except RamlError:
                continue
            pending.append(iter(shape.any_of or ()) if selected is None else iter((selected,)))
        else:
            yield current


def children(base: BaseShape, value: ValueNode) -> Iterator[TypedChild]:
    """The declarations governing each immediate child, in branch/data order.

    Invalid values retain known field types; additional fields have none. Array
    items are identified by integer indices, not invented property declarations.
    """
    for branch in branches(base, value):
        shape = branch.shape
        if isinstance(shape, ObjectShape) and value.mapping is not None:
            for entry in value.mapping.entries:
                prop = shape.property_for(entry.key)
                if prop is not None:
                    yield TypedChild(entry.key, entry.value, prop.base, prop, entry.key_pos)
        elif isinstance(shape, ArrayShape) and shape.items is not None and value.sequence is not None:
            for index, item in enumerate(value.sequence.items):
                yield TypedChild(index, item.value, shape.items)
