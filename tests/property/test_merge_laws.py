"""The merge laws of docs/14-testing.md § 4, over generated trees.

Three laws: identity, target-wins, and purity (docs/08 § 1). A hand-written
example proves a merge handles the case someone thought of; these prove it
handles the shapes nobody did — a key present at three depths, a sequence of
mappings, an opaque facet holding a mapping that looks like RAML structure.

Target-wins is the one worth stating carefully. "Target wins" does not mean the target
value survives unchanged: a mapping recurses and a sequence unions. What must
hold is that **every target key is still present**, and that a target scalar
still has its own value.
"""

from __future__ import annotations

from hypothesis import given, settings

from fastraml.parser.structural_merge import merge_structural
from fastraml.parser.templates import iter_nodes
from fastraml.registry import ParseCtx
from fastraml.yamlnode import Node, NodeKind, pairs
from tests.trees import mapping_trees, snapshot

SCOPE = ParseCtx()

# A small alphabet on purpose: keys must collide often enough that the
# in-both branch of the merge is exercised, and so must sequence items.
mappings = mapping_trees(
    ['a', 'b', 'c', 'example', 'default', 'type'], ['1', '2', 'x', 'y'], max_leaves=12, max_entries=4
)


class TestLaw2Identity:
    @given(node=mappings)
    def test_merging_with_nothing_yields_the_same_object(self, node: Node):
        assert merge_structural(node, None, SCOPE) is node
        assert merge_structural(None, node, SCOPE) is node


class TestLaw3TargetWins:
    @given(target=mappings, source=mappings)
    @settings(max_examples=300)
    def test_no_target_key_is_lost(self, target: Node, source: Node):
        merged = merge_structural(target, source, SCOPE)
        assert merged is not None
        target_keys = [key.value for key, _ in pairs(target)]
        merged_keys = [key.value for key, _ in pairs(merged)]
        assert merged_keys[: len(target_keys)] == target_keys, 'target keys survive, in target order'

    @given(target=mappings, source=mappings)
    @settings(max_examples=300)
    def test_a_target_scalar_keeps_its_own_value(self, target: Node, source: Node):
        merged = merge_structural(target, source, SCOPE)
        assert merged is not None
        merged_values = {key.value: value for key, value in pairs(merged)}
        for key, value in pairs(target):
            if value.kind is NodeKind.SCALAR:
                assert merged_values[key.value].value == value.value

    @given(target=mappings, source=mappings)
    @settings(max_examples=300)
    def test_every_source_key_is_present_too(self, target: Node, source: Node):
        merged = merge_structural(target, source, SCOPE)
        assert merged is not None
        merged_keys = {key.value for key, _ in pairs(merged)}
        assert {key.value for key, _ in pairs(source)} <= merged_keys


class TestLaw4Purity:
    @given(target=mappings, source=mappings)
    @settings(max_examples=300)
    def test_neither_input_is_mutated(self, target: Node, source: Node):
        before = snapshot(target), snapshot(source)
        merge_structural(target, source, SCOPE, {})
        assert (snapshot(target), snapshot(source)) == before


class TestNodeIdentityIsPreserved:
    """Invariant I10, which the provenance overlay's identity keys depend on."""

    @given(target=mappings, source=mappings)
    @settings(max_examples=300)
    def test_every_merged_scalar_is_one_of_the_inputs_own(self, target: Node, source: Node):
        merged = merge_structural(target, source, SCOPE)
        assert merged is not None
        originals = {id(node) for tree in (target, source) for node in iter_nodes(tree)}
        for node in iter_nodes(merged):
            if node.kind is NodeKind.SCALAR:
                assert id(node) in originals, 'a copied scalar would drop its provenance mark'
