"""Laws of the extension merge over generated trees (docs/19 § 3 and § 5.3).

Four laws. Purity and identity are the template merge's (invariants I9, I10).
Restating is the overlay rule of docs/19 § 4.1: a tree applied to itself
changes nothing, so it is neither a difference nor authored. Provenance is
what document marks rest on: no node the extension document wrote reaches the
target tree unmarked, or a later reader would name the wrong file for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import given, settings

from fastraml.parser.extension_merge import merge_extension
from fastraml.parser.templates import iter_nodes
from tests.trees import mapping, mapping_trees, snapshot

if TYPE_CHECKING:
    from fastraml.yamlnode import Node

LOCATION = 'file:///api/extension.raml'

# Keys chosen to reach every branch: grammar positions, synonyms, conflicts,
# data positions, multi-value keys, and names that look like facets.
_KEYS = [
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

mappings = mapping_trees(_KEYS, ['1', '2', 'x'], max_leaves=14, max_entries=5)


def merge(target: Node, extension: Node, *, overlay: bool = False):
    marked: set[int] = set()

    def mark(node: Node) -> None:
        marked.update(id(each) for each in iter_nodes(node))

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
        result, marked = merge(target, mapping([]))
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
        written = {id(node) for node in iter_nodes(extension)}
        unmarked = [node for node in iter_nodes(result.tree) if id(node) in written and id(node) not in marked]
        assert not unmarked

    @given(target=mappings, extension=mappings)
    @settings(max_examples=300)
    def test_no_target_node_is_marked(self, target: Node, extension: Node):
        _, marked = merge(target, extension)
        assert not any(id(node) in marked for node in iter_nodes(target))
