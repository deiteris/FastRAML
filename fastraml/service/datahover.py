"""Source spans in typed DataNodes, shared by all value-bearing hover sites.

Bindings come from the passes; object matching and discriminator selection
come from the model (docs/21 § 4.2). This index never binds a name or validates
an entire value to guess which union alternative the author meant.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastraml.errors import RamlError
from fastraml.types.base import Property
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from fastraml.datanode import DataNode, ValueNode
    from fastraml.positions import Position
    from fastraml.types.base import BaseShape


@dataclass(frozen=True, slots=True, eq=False)
class DataRoot:
    data: DataNode
    base: BaseShape
    name: str
    role: str


@dataclass(frozen=True, slots=True, eq=False)
class DataTarget:
    uri: str
    span: Position
    base: BaseShape
    name: str
    role: str
    required: bool | None = None
    is_key: bool = False


class DataHover:
    """A lazy, once-per-snapshot token index over typed structured values."""

    __slots__ = ('_roots', '_starts', '_targets')

    def __init__(self, roots: Iterable[DataRoot]) -> None:
        self._roots: Iterable[DataRoot] | None = roots
        self._targets: dict[str, list[DataTarget]] = {}
        self._starts: dict[str, list[tuple[int, int]]] = {}

    def at(self, uri: str, line: int, column: int) -> list[DataTarget]:
        if self._roots is not None:
            self._index()
        starts = self._starts.get(uri, [])
        end = bisect_right(starts, (line, column))
        if not end:
            return []
        same = self._targets[uri][bisect_left(starts, starts[end - 1]) : end]
        return [
            target
            for target in same
            if (target.span.line, target.span.column) <= (line, column) < (target.span.end_line, target.span.end_column)
        ]

    def _index(self) -> None:
        targets: dict[str, list[DataTarget]] = {}
        seen = set()
        for root in self._roots or ():
            for target in _targets(root):
                key = (target.uri, target.span, target.base.id, target.name, target.role)
                if key in seen or not target.uri or not target.span.is_known:
                    continue
                seen.add(key)
                targets.setdefault(target.uri, []).append(target)
        for uri, found in targets.items():
            found.sort(key=lambda target: (target.span.line, target.span.column))
            self._starts[uri] = [(target.span.line, target.span.column) for target in found]
        self._targets = targets
        self._roots = None

    def in_range(self, uri: str, span: Position) -> list[DataTarget]:
        """Key targets for inline hints, using the same lazy source index."""
        if self._roots is not None:
            self._index()
        found = self._targets.get(uri, [])
        starts = self._starts.get(uri, [])
        # A hint is anchored after its key, which may start before the range.
        begin = bisect_left(starts, (span.line, 1))
        end = bisect_right(starts, (span.end_line, span.end_column))
        return [target for target in found[begin:end] if target.is_key]


def _targets(root: DataRoot) -> Iterator[DataTarget]:
    if root.data.key_location is not None and root.data.key_pos.is_known:
        yield DataTarget(root.data.key_location, root.data.key_pos, root.base, root.name, root.role, is_key=True)
    pending: list[tuple[ValueNode, BaseShape, str, str, bool | None]] = [
        (root.data.value, root.base, root.name, root.role, None)
    ]
    seen: set[tuple[ValueNode, BaseShape]] = set()
    while pending:
        value, base, name, role, required = pending.pop()
        if (value, base) in seen:
            continue
        seen.add((value, base))
        uri = value.location or root.data.location
        # Collections have child key/value spans. Indexing their whole extent
        # would make whitespace and unknown fields inherit the parent hover.
        # An encoded JSON scalar has no nested spans: its root alone is known.
        if value.is_scalar or (value.position.is_known and not _has_child_spans(value)):
            yield DataTarget(uri, value.position, base, name, role, required)
        for branch in _branches(base, value):
            shape = branch.shape
            if isinstance(shape, ObjectShape) and value.mapping is not None:
                for entry in value.mapping.entries:
                    prop = shape.property_for(entry.key)
                    if prop is None:
                        continue
                    presence = prop.required if isinstance(prop, Property) else None
                    child_role = 'object property' if isinstance(prop, Property) else 'pattern-matched property'
                    yield DataTarget(uri, entry.key_pos, prop.base, entry.key, child_role, presence, is_key=True)
                    pending.append((entry.value, prop.base, entry.key, child_role, presence))
            elif isinstance(shape, ArrayShape) and shape.items is not None and value.sequence is not None:
                pending.extend(
                    (item.value, shape.items, '<item>', 'array item', None) for item in reversed(value.sequence.items)
                )


def _has_child_spans(value: ValueNode) -> bool:
    if value.mapping is not None:
        return any(entry.key_pos.is_known for entry in value.mapping.entries)
    if value.sequence is not None:
        return any(item.value_pos.is_known for item in value.sequence.items)
    return False


def _branches(base: BaseShape, value: ValueNode) -> Iterator[BaseShape]:
    pending = [base]
    seen: set[BaseShape] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        shape = current.shape
        if isinstance(shape, RecursiveShape):
            pending.append(shape.head)
        elif current.alias is not None:
            pending.append(current.alias)
        elif isinstance(shape, UnionShape):
            try:
                selected = shape.select(value.raw)
            except RamlError:
                continue  # a discriminator that names no alternative selects none
            pending.extend(reversed(shape.any_of or ()) if selected is None else (selected,))
        else:
            yield current
