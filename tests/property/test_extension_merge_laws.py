"""Laws of the extension merge over generated trees (docs/19 § 3 and § 5.3).

Four laws. Purity and identity are the template merge's (invariants I9, I10).
Restating is the overlay rule of docs/19 § 4.1: a tree applied to itself
changes nothing, so it is neither a difference nor authored. Provenance is
what document marks rest on: no node the extension document wrote reaches the
target tree unmarked, or a later reader would name the wrong file for it.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from fastraml.parser.extension_merge import merge_extension
from fastraml.yamlnode import TAG_MAP, TAG_SEQ, TAG_STR, Node, NodeKind

LOCATION = 'file:///api/extension.raml'

# Keys chosen to reach every branch: grammar positions, synonyms, conflicts,
# data positions, multi-value keys, and names that look like facets.
_KEYS = st.sampled_from(
    [
        'a',
        'description',
        '/r',
        'get',
        'types',
        'schemas',
        'type',
        'schema',
        'queryString',
        'queryParameters',
        'properties',
        'example',
        'examples',
        'enum',
        'is',
        'protocols',
        '(note)',
    ]
)
_VALUES = st.sampled_from(['1', '2', 'x'])


def _scalar(value: str) -> Node:
    return Node(NodeKind.SCALAR, TAG_STR, value)


def _mapping(entries: list[tuple[str, Node]]) -> Node:
    content: list[Node] = []
    seen: set[str] = set()
    for key, value in entries:
        if key not in seen:
            seen.add(key)
            content += (_scalar(key), value)
    return Node(NodeKind.MAPPING, TAG_MAP, '', content)


def _sequence(items: list[Node]) -> Node:
    return Node(NodeKind.SEQUENCE, TAG_SEQ, '', items)


nodes = st.recursive(
    _VALUES.map(_scalar),
    lambda children: st.one_of(
        st.lists(st.tuples(_KEYS, children), max_size=4).map(_mapping),
        st.lists(children, max_size=3).map(_sequence),
    ),
    max_leaves=14,
)
mappings = st.lists(st.tuples(_KEYS, nodes), max_size=5).map(_mapping)


def snapshot(node: Node):
    return (node.kind, node.tag, node.value, [snapshot(child) for child in node.content])


def walk(node: Node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack += current.content


def merge(target: Node, extension: Node, *, overlay: bool = False):
    marked: set[int] = set()

    def mark(node: Node) -> None:
        marked.update(id(each) for each in walk(node))

    return merge_extension(target, extension, location=LOCATION, overlay=overlay, mark=mark), marked


class TestPurity:
    @given(target=mappings, extension=mappings)
    @settings(max_examples=300)
    def test_neither_input_is_mutated(self, target: Node, extension: Node):
        before = (snapshot(target), snapshot(extension))
        merge(target, extension)
        assert (snapshot(target), snapshot(extension)) == before


class TestIdentity:
    @given(target=mappings)
    def test_an_empty_extension_returns_the_target(self, target: Node):
        result, marked = merge(target, _mapping([]))
        assert result.tree is target
        assert not marked


class TestRestating:
    @given(target=mappings)
    @settings(max_examples=300)
    def test_a_tree_applied_to_itself_changes_nothing(self, target: Node):
        result, marked = merge(target, target, overlay=True)
        assert result.tree is target
        assert result.error is None
        assert not marked


class TestProvenance:
    @given(target=mappings, extension=mappings)
    @settings(max_examples=300)
    def test_every_extension_node_in_the_result_is_marked(self, target: Node, extension: Node):
        result, marked = merge(target, extension)
        written = {id(node) for node in walk(extension)}
        unmarked = [node for node in walk(result.tree) if id(node) in written and id(node) not in marked]
        assert not unmarked

    @given(target=mappings, extension=mappings)
    @settings(max_examples=300)
    def test_no_target_node_is_marked(self, target: Node, extension: Node):
        _, marked = merge(target, extension)
        assert not any(id(node) in marked for node in walk(target))
