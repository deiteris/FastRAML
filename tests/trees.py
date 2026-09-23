"""YAML node trees for the merge tests: builders, generators, and a snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import strategies as st

from fastraml.yamlnode import TAG_MAP, TAG_SEQ, TAG_STR, Node, NodeKind

if TYPE_CHECKING:
    from collections.abc import Sequence


def scalar(value: str) -> Node:
    return Node(NodeKind.SCALAR, TAG_STR, value)


def mapping(entries: list[tuple[str, Node]]) -> Node:
    """A mapping of the first value under each key.

    YAML permits duplicate keys; RAML does not, and `compose` records them for a
    decoder to reject, so a merge never sees a pair.
    """
    content: list[Node] = []
    seen: set[str] = set()
    for key, value in entries:
        if key not in seen:
            seen.add(key)
            content += (scalar(key), value)
    return Node(NodeKind.MAPPING, TAG_MAP, '', content)


def sequence(items: list[Node]) -> Node:
    return Node(NodeKind.SEQUENCE, TAG_SEQ, '', items)


def mapping_trees(
    keys: Sequence[str], values: Sequence[str], *, max_leaves: int, max_entries: int
) -> st.SearchStrategy[Node]:
    """Mapping trees over a small alphabet, so keys and items collide often.

    Both merges are only ever called on two mappings, so the top is one.
    """
    key = st.sampled_from(keys)
    nodes = st.recursive(
        st.sampled_from(values).map(scalar),
        lambda children: st.one_of(
            st.lists(st.tuples(key, children), max_size=4).map(mapping),
            st.lists(children, max_size=3).map(sequence),
        ),
        max_leaves=max_leaves,
    )
    return st.lists(st.tuples(key, nodes), max_size=max_entries).map(mapping)


def snapshot(node: Node) -> tuple[object, ...]:
    """Everything about a tree a merge is forbidden to change: kinds, tags, values."""
    return (node.kind, node.tag, node.value, [snapshot(child) for child in node.content])
