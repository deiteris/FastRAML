"""Whether two entries are identical, and where they first differ (docs/20 § 4).

`node_value_equal` with two changes: an `!include` compares what it points at,
not its text, and the result names the first differing pair so a conflict can
say where the entries part.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastraml.errors import ErrorKind, RamlError
from fastraml.parser.includes import resolve_ref_uri, strip_uri_suffix
from fastraml.yamlnode import TAG_INCLUDE, TAG_STR, Node, NodeKind, compose, decode_source

if TYPE_CHECKING:
    from fastraml.registry import Raml

__all__ = ['Difference', 'IncludeReader', 'Side', 'first_difference']

#: Include targets composed as YAML, as in `parser/includes.py`.
_YAML_EXTENSIONS: Final = frozenset({'.raml', '.yaml', '.yml', '.json'})


@dataclass(frozen=True, slots=True)
class Difference:
    """The first pair that differs, and the key path from the entry to it."""

    left: Node
    right: Node
    path: tuple[str, ...]


class IncludeReader:
    """Reads `!include` targets through one input's parse.

    A target the parse already read comes from its caches. One it never read,
    such as an include inside a trait no method applies, is loaded through the
    same sandboxed loader and composed once.
    """

    __slots__ = ('_loaded', 'raml')

    def __init__(self, raml: Raml) -> None:
        self.raml = raml
        self._loaded: dict[str, Node] = {}

    def target(self, node: Node, file_uri: str) -> str:
        """The absolute URI an include argument names, with any `#` or `?` suffix."""
        return resolve_ref_uri(self.raml, node.value, file_uri, node.position)

    def content(self, target: str) -> Node:
        """The composed content of `target`, or its text as a string scalar."""
        raml = self.raml
        cached = raml.include_nodes.get(target) or raml.source_nodes.get(target) or self._loaded.get(target)
        if cached is not None:
            return cached
        bare = strip_uri_suffix(target)
        limit = raml.max_include_size
        try:
            data = raml.loader.load(bare, max_bytes=limit if limit > 0 else None)
        except OSError as err:
            raise RamlError.wrap('include', err, bare, kind=ErrorKind.LOADING, info={'path': bare}) from err
        text = decode_source(data)
        if posixpath.splitext(bare)[1].lower() in _YAML_EXTENSIONS:
            loaded = compose(text, uri=bare, max_depth=raml.max_depth)
        else:
            loaded = Node(NodeKind.SCALAR, TAG_STR, text)
        self._loaded[target] = loaded
        return loaded


def _suffix(uri: str) -> str:
    return uri[len(strip_uri_suffix(uri)) :]


@dataclass(frozen=True, slots=True)
class Side:
    """One side of a comparison: a node, the file it was read from, and its input's reader."""

    node: Node
    file: str
    reader: IncludeReader


type _Pair = tuple[Node, str, Node, str, tuple[str, ...]]


def _through_includes(pair: _Pair, left: IncludeReader, right: IncludeReader) -> _Pair | Difference | None:
    """The pair with each include replaced by its target; `None` when both name one target."""
    one, one_file, other, other_file, path = pair
    one_target = left.target(one, one_file) if one.tag == TAG_INCLUDE else ''
    other_target = right.target(other, other_file) if other.tag == TAG_INCLUDE else ''
    if one_target and one_target == other_target:
        return None
    # A `#` or `?` suffix selects part of the file, so two differently selected
    # parts of one file are not compared by content.
    if _suffix(one_target) != _suffix(other_target):
        return Difference(one, other, path)
    if one_target:
        one, one_file = left.content(one_target), strip_uri_suffix(one_target)
    if other_target:
        other, other_file = right.content(other_target), strip_uri_suffix(other_target)
    return one, one_file, other, other_file, path


def _children(pair: _Pair) -> list[_Pair]:
    """The child pairs of two containers of one kind and length, in document order."""
    one, one_file, other, other_file, path = pair
    content, others = one.content, other.content
    if one.kind is NodeKind.MAPPING:
        children: list[_Pair] = []
        for index in range(0, len(content), 2):
            key = content[index]
            children.append((key, one_file, others[index], other_file, path))
            children.append((content[index + 1], one_file, others[index + 1], other_file, (*path, key.value)))
        return children
    return [
        (item, one_file, other_item, other_file, (*path, str(index)))
        for index, (item, other_item) in enumerate(zip(content, others, strict=True))
    ]


def first_difference(left: Side, right: Side) -> Difference | None:
    """The first differing pair in document order, or `None` when identical.

    Scalars compare by tag and value, containers item by item and in order;
    positions are ignored. An include is replaced by its target before
    comparing, and two includes of one target are equal unread.
    """
    # The parser rejects include cycles only in targets it read. A target it
    # never read, inside a template nothing applies, is read here first, so a
    # cycle through it is a difference rather than a loop.
    limit = left.reader.raml.max_depth
    followed: set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    stack: list[_Pair] = [(left.node, left.file, right.node, right.file, ())]
    while stack:
        pair = stack.pop()
        one, one_file, other, other_file, path = pair
        if one is other and one_file == other_file:
            continue
        if len(path) > limit:
            return Difference(one, other, path)
        if TAG_INCLUDE in {one.tag, other.tag}:
            step = (one_file, one.value, other_file, other.value, path)
            if step in followed:
                return Difference(one, other, path)
            followed.add(step)
            through = _through_includes(pair, left.reader, right.reader)
            if isinstance(through, Difference):
                return through
            if through is not None:
                stack.append(through)
            continue
        if one.kind is not other.kind or len(one.content) != len(other.content):
            return Difference(one, other, path)
        if one.kind is NodeKind.SCALAR:
            if one.tag != other.tag or one.value != other.value:
                return Difference(one, other, path)
            continue
        # Reversed, so the stack pops them in document order.
        stack.extend(reversed(_children(pair)))
    return None
