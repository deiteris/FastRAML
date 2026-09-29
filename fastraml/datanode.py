"""`DataNode` — arbitrary user data with positions at every depth.

RAML has places where any user value may appear: `example`, `examples.*`,
`default`, `enum` members, annotation values, custom-facet values,
`discriminatorValue`. These are data, not declarations, but they still need
source positions, because "this example does not validate" has to point at the
offending key three levels down.

`ValueNode.raw` holds the plain Python projection and is computed once during
construction: every stored value is validated at least once when validation is
enabled, so laziness would only add a branch. Validation and serialization use
`raw`; diagnostics use the position-bearing structure.

See docs/03-yaml-and-io.md § 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from fastraml.errors import ErrorKind, RamlError, Trace
from fastraml.parser.includes import IncludeInfo, resolve_include
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import (
    TAG_BOOL,
    TAG_FLOAT,
    TAG_INCLUDE,
    TAG_INT,
    TAG_NULL,
    Node,
    NodeKind,
    node_error,
)

if TYPE_CHECKING:
    from fastraml.registry import Raml

__all__ = [
    'DataNode',
    'MappingEntry',
    'MappingValue',
    'SequenceItem',
    'SequenceValue',
    'ValueNode',
    'at_value',
    'included_data_node',
    'locate',
    'make_data_node',
    'parse_int',
    'value_node_of',
]

#: The loader resolves YAML 1.2, where `true` is the only true. `yes`, `on`
#: and `y` are strings and never reach here tagged `!!bool`.
_TRUE_SCALARS: Final = frozenset({'true'})

#: Only matching outer delimiters identify an inline JSON data value.
_INLINE_JSON_DELIMITERS: Final = {'{': '}', '[': ']', '"': '"'}

#: Float scalars YAML spells in words rather than digits.
_SPECIAL_FLOATS: Final = {
    '.inf': float('inf'),
    '+.inf': float('inf'),
    '-.inf': float('-inf'),
    '.nan': float('nan'),
}


@dataclass(slots=True, eq=False)
class MappingEntry:
    """One key/value pair of a `MappingValue`, with the key's own position."""

    key: str
    value: ValueNode
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN


@dataclass(slots=True, eq=False)
class MappingValue:
    entries: list[MappingEntry] = field(default_factory=list)


@dataclass(slots=True, eq=False)
class SequenceItem:
    value: ValueNode
    value_pos: Position = UNKNOWN


@dataclass(slots=True, eq=False)
class SequenceValue:
    items: list[SequenceItem] = field(default_factory=list)


class ValueNode:
    """One structured value. Exactly one of `mapping`/`sequence`/scalar applies.

    A scalar `None` is a legitimate value (YAML null), so "is this a scalar?" is
    answered by `is_scalar` rather than by testing `scalar` for `None`.
    """

    __slots__ = ('mapping', 'raw', 'scalar', 'sequence')

    def __init__(
        self,
        *,
        raw: Any,
        scalar: Any = None,
        mapping: MappingValue | None = None,
        sequence: SequenceValue | None = None,
    ) -> None:
        self.scalar = scalar
        self.mapping = mapping
        self.sequence = sequence
        self.raw = raw

    def __repr__(self) -> str:
        return f'ValueNode({self.raw!r})'

    @property
    def is_scalar(self) -> bool:
        return self.mapping is None and self.sequence is None


def value_node_of(value: Any) -> ValueNode:
    """Wrap a plain Python value — one decoded from inline JSON, or a test's.

    Positions are unavailable for such values, so every nested position is
    `UNKNOWN`.
    """
    if isinstance(value, dict):
        entries = [MappingEntry(key=str(key), value=value_node_of(child)) for key, child in value.items()]
        return ValueNode(mapping=MappingValue(entries), raw=value)
    if isinstance(value, list):
        items = [SequenceItem(value=value_node_of(child)) for child in value]
        return ValueNode(sequence=SequenceValue(items), raw=value)
    return ValueNode(scalar=value, raw=value)


@dataclass(slots=True, eq=False)
class DataNode:
    """A user value, its provenance, and the positions of the pair that held it."""

    value: ValueNode
    location: str
    include: IncludeInfo | None = None
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __str__(self) -> str:
        return str(self.value.raw)

    @property
    def raw(self) -> Any:
        return self.value.raw


def locate(data: DataNode, path: str, key: str | None = None) -> Position:
    """Where the value at `path` (`$.a[0]`, docs/10 § 3) was written in `data`,
    or its key when `key` names one of its entries: the nearest known position
    on the way down, `UNKNOWN` above it.

    A key is matched against the entries rather than split out of the path,
    since a key may hold a `.` itself. The root of an included value has no
    position here: `value_pos` is the `!include`'s, in the including file.
    """
    node: ValueNode | None = data.value
    position = UNKNOWN if data.include is not None else data.value_pos
    rest = path[1:] if path.startswith('$') else ''
    while rest and node is not None:
        if rest[0] == '[' and node.sequence is not None and (end := rest.find(']')) > 0:
            index = int(rest[1:end])
            items = node.sequence.items
            if not 0 <= index < len(items):
                break
            item = items[index]
            node, rest, position = item.value, rest[end + 1 :], item.value_pos
        elif rest[0] == '.' and node.mapping is not None:
            entry = _entry(node.mapping, rest[1:])
            if entry is None:
                break
            node, rest, position = entry.value, rest[1 + len(entry.key) :], entry.value_pos
        else:
            break
    if key is not None and not rest and node is not None and node.mapping is not None:
        for entry in node.mapping.entries:
            if entry.key == key:
                return entry.key_pos
    return position


def at_value(err: RamlError, data: DataNode) -> RamlError:
    """`err`, raised by `validate_at` against a declaration, placed at the value
    in `data` each frame rejected. Where the frame was, the constraint, becomes
    its `origin` (docs/11 § 3).

    A frame on a key names it in `info['property']`, and is placed at that key.
    """
    return RamlError(_placed(err.head, data), tuple(at_value(sibling, data) for sibling in err.siblings))


def _placed(frame: Trace, data: DataNode) -> Trace:
    cause = None if frame.cause is None else _placed(frame.cause, data)
    path, key = frame.info.get('path'), frame.info.get('property')
    if frame.kind is not ErrorKind.VALIDATING or not isinstance(path, str):
        return Trace(frame.message, frame.location, frame.position, frame.kind, frame.info, cause, frame.origin)
    position = locate(data, path, key if isinstance(key, str) else None)
    origin = None if frame.position is None else Trace('declared here', frame.location, frame.position)
    placed = position if position.is_known else None
    return Trace(frame.message, data.location, placed, frame.kind, frame.info, cause, origin)


def _entry(mapping: MappingValue, rest: str) -> MappingEntry | None:
    """The longest key `rest` starts with that ends at a step boundary."""
    found: MappingEntry | None = None
    for entry in mapping.entries:
        key = entry.key
        if (
            rest.startswith(key)
            and rest[len(key) : len(key) + 1] in ('', '.', '[')
            and (found is None or len(key) > len(found.key))
        ):
            found = entry
    return found


def included_data_node(raml: Raml, key_node: Node | None, value_node: Node, target: str, content: Node) -> DataNode:
    """A value an `!include` supplied, once resolved: located in the included
    file, so a bad example reports that file's path.
    """
    return DataNode(
        value=_to_value(raml, content, target, {target}),
        location=target,
        include=IncludeInfo(path=value_node.value, abs_uri=target),
        key_pos=key_node.position if key_node is not None else UNKNOWN,
        value_pos=value_node.position,
    )


def make_data_node(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> DataNode:
    """Build a `DataNode` from a key/value pair of the document.

    Three forms are recognised, in this order: an `!include`, whose content is
    read and whose `location` becomes the *included* file so that a bad example
    reports that file's path; a scalar enclosed by matching JSON delimiters,
    decoded once without reinterpreting its result; and ordinary YAML.
    """
    location = raml.document_location(value_node, location)
    if value_node.tag == TAG_INCLUDE:
        target, content = resolve_include(raml, value_node, location)
        return included_data_node(raml, key_node, value_node, target, content)

    if (
        value_node.kind is NodeKind.SCALAR
        and (closing := _INLINE_JSON_DELIMITERS.get(value_node.value[:1])) is not None
        and len(text := value_node.value.rstrip(' \t\r\n')) > 1
        and text.endswith(closing)
    ):
        import json  # noqa: PLC0415 - most RAML data is already represented by YAML nodes

        try:
            decoded = json.loads(value_node.value)
        except ValueError as err:
            raise node_error('invalid inline JSON', location, value_node, info={'error': str(err)}) from err
        value = value_node_of(decoded)
    else:
        value = _to_value(raml, value_node, location, None)

    return DataNode(
        value=value,
        location=location,
        key_pos=key_node.position if key_node is not None else UNKNOWN,
        value_pos=value_node.full_position,
    )


def _to_value(raml: Raml, node: Node, location: str, visited: set[str] | None) -> ValueNode:
    if node.kind is NodeKind.MAPPING:
        entries: list[MappingEntry] = []
        raw_map: dict[str, Any] = {}
        content = node.content
        for index in range(0, len(content), 2):
            key, value = content[index], content[index + 1]
            child = _to_value(raml, value, location, visited)
            entries.append(
                MappingEntry(key=key.value, value=child, key_pos=key.position, value_pos=value.full_position)
            )
            raw_map[key.value] = child.raw
        return ValueNode(mapping=MappingValue(entries), raw=raw_map)

    if node.kind is NodeKind.SEQUENCE:
        items: list[SequenceItem] = []
        raw_list: list[Any] = []
        for item in node.content:
            child = _to_value(raml, item, location, visited)
            items.append(SequenceItem(value=child, value_pos=item.full_position))
            raw_list.append(child.raw)
        return ValueNode(sequence=SequenceValue(items), raw=raw_list)

    return _scalar_to_value(raml, node, location, visited)


def _scalar_to_value(raml: Raml, node: Node, location: str, visited: set[str] | None) -> ValueNode:
    if node.tag != TAG_INCLUDE:
        value = scalar_value(node)
        return ValueNode(scalar=value, raw=value)

    target, content = resolve_include(raml, node, location)
    # A scalar include may itself include, so the chain is what needs cycle
    # detection; fragment-level cycles are legal and handled by the fragment
    # cache instead. See docs/03-yaml-and-io.md § 4.3.
    if visited is None:
        visited = set()
    elif target in visited:
        raise node_error('circular include detected', location, node, info={'path': target})
    visited.add(target)
    try:
        return _to_value(raml, content, target, visited)
    finally:
        visited.discard(target)


def scalar_value(node: Node) -> Any:
    """A scalar node's Python value, taken from its tag and its literal text.

    `!!timestamp` and every unrecognised tag keep the raw text: RAML wants the
    literal form of a `date-only` example, and the type layer parses it. Numbers
    are converted from the text, never through an intermediate `float`.

    A tag whose text will not convert also keeps the raw text rather than
    raising: `08` resolves as `!!int` but has no octal reading, and an explicit
    `!!int abc` has no reading at all.
    """
    tag = node.tag
    if tag == TAG_NULL:
        return None
    if tag == TAG_BOOL:
        return node.value.lower() in _TRUE_SCALARS
    try:
        if tag == TAG_INT:
            return parse_int(node.value)
        if tag == TAG_FLOAT:
            return _parse_float(node.value)
    except ValueError:
        return node.value
    return node.value


def parse_int(text: str) -> int:
    cleaned = text.replace('_', '')
    sign = 1
    if cleaned[:1] in ('+', '-'):
        sign = -1 if cleaned[0] == '-' else 1
        cleaned = cleaned[1:]
    prefix = cleaned[:2].lower()
    if prefix in ('0x', '0o', '0b'):
        return sign * int(cleaned, 0)
    if cleaned[:1] == '0' and len(cleaned) > 1:
        # A bare leading zero is octal, as in PyYAML's own constructor and in
        # go-yaml's `strconv.ParseInt(plain, 0, 64)`: `017` is 15, not 17.
        # `08` has no octal reading, so `int` raises and `scalar_value` keeps the
        # text — which is what go-yaml does with it too.
        return sign * int(cleaned, 8)
    return sign * int(cleaned, 10)


def _parse_float(text: str) -> float:
    cleaned = text.replace('_', '')
    special = _SPECIAL_FLOATS.get(cleaned.lower())
    if special is not None:
        return special
    return float(cleaned)
