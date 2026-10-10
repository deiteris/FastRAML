"""Source spans in typed DataNodes, shared by all value-bearing hover sites.

Bindings come from the passes; object matching and discriminator selection
come from the model (docs/21 § 4.2). This index never binds a name or validates
an entire value to guess which union alternative the author meant.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastraml.types.base import Property
from fastraml.types.navigation import children

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from fastraml.datanode import DataNode, ValueNode
    from fastraml.positions import Position
    from fastraml.types.base import BaseShape
    from fastraml.types.navigation import TypedChild


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
    value, base, name, role = root.data.value, root.base, root.name, root.role
    required: bool | None = None
    pending: list[tuple[Iterator[TypedChild], str]] = []
    seen: set[tuple[ValueNode, BaseShape]] = set()
    while True:
        if (value, base) not in seen:
            seen.add((value, base))
            uri = value.location or root.data.location
            # Collections have child key/value spans. Indexing their whole extent
            # would make whitespace and unknown fields inherit the parent hover.
            # An encoded JSON scalar has no nested spans: its root alone is known.
            if value.is_scalar or (value.position.is_known and not _has_child_spans(value)):
                yield DataTarget(uri, value.position, base, name, role, required)
            if not value.is_scalar:
                pending.append((children(base, value), uri))
        # Resume one sibling at a time; frames borrow the model's child iterator
        # instead of buffering every typed child and copying it into a worklist.
        while pending:
            siblings, uri = pending[-1]
            child = next(siblings, None)
            if child is None:
                pending.pop()
                continue
            prop = child.declaration
            required = prop.required if isinstance(prop, Property) else None
            if prop is None:
                name, role = '<item>', 'array item'
            else:
                name = str(child.name)
                role = 'object property' if isinstance(prop, Property) else 'pattern-matched property'
                yield DataTarget(uri, child.key_pos, child.base, name, role, required, is_key=True)
            value, base = child.value, child.base
            break
        else:
            return


def _has_child_spans(value: ValueNode) -> bool:
    if value.mapping is not None:
        return any(entry.key_pos.is_known for entry in value.mapping.entries)
    if value.sequence is not None:
        return any(item.value_pos.is_known for item in value.sequence.items)
    return False
