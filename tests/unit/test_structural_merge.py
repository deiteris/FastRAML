"""The spec's merging algorithm (docs/08 section 4).

What is pinned here is the four things a merge can silently get wrong: it can
mutate an input, it can allocate a fresh child and break a provenance key, it
can recurse into user data, and it can lose a target key. Each has a test that
names the rule rather than the example.
"""

from __future__ import annotations

from pyraml.parser.structural_merge import (
    copy_overlay,
    mark_graft,
    merge_structural,
    node_value_equal,
)
from pyraml.registry import ParseCtx
from pyraml.yamlnode import NodeKind, compose, pairs

LOCATION = 'file:///a.raml'
SCOPE = ParseCtx()


def tree(text: str):
    return compose(text, uri=LOCATION)


def merge(target: str, source: str, overlay=None):
    return merge_structural(tree(target), tree(source), SCOPE, overlay)


def snapshot(node):
    """A structure the merge must not be able to change: kinds, tags, values."""
    return (node.kind, node.tag, node.value, [snapshot(child) for child in node.content])


def keys(node) -> list[str]:
    return [key.value for key, _ in pairs(node)]


def flat(node) -> dict[str, object]:
    """A mapping as `{key: scalar value or nested dict or list}`."""
    result: dict[str, object] = {}
    for key, value in pairs(node):
        if value.kind is NodeKind.MAPPING:
            result[key.value] = flat(value)
        elif value.kind is NodeKind.SEQUENCE:
            result[key.value] = [item.value for item in value.content]
        else:
            result[key.value] = value.value
    return result


class TestTheThreeSpecRules:
    def test_a_target_only_key_is_unchanged(self):
        merged = merge('a: 1\n', 'b: 2\n')
        assert flat(merged)['a'] == '1'

    def test_a_source_only_key_is_added(self):
        merged = merge('a: 1\n', 'b: 2\n')
        assert flat(merged) == {'a': '1', 'b': '2'}

    def test_a_scalar_in_both_keeps_the_target_s_value(self):
        assert flat(merge('a: mine\n', 'a: theirs\n')) == {'a': 'mine'}

    def test_an_object_in_both_recurses(self):
        merged = merge('a: {x: mine}\n', 'a: {x: theirs, y: added}\n')
        assert flat(merged) == {'a': {'x': 'mine', 'y': 'added'}}

    def test_a_collection_in_both_unions_target_first(self):
        # Spec section Effect on Collections: "the actual enum is [mac, unix, win]".
        merged = merge('enum: [mac, unix]\n', 'enum: [unix, win]\n')
        assert flat(merged) == {'enum': ['mac', 'unix', 'win']}

    def test_ordering_is_target_keys_then_source_only_keys(self):
        merged = merge('b: 1\na: 2\n', 'z: 3\na: 4\ny: 5\n')
        assert keys(merged) == ['b', 'a', 'z', 'y']


class TestKindDisagreement:
    def test_the_explicit_node_wins(self):
        # A method that wrote `body: string` does not acquire the trait's
        # mapping of media types underneath it.
        merged = merge('body: string\n', 'body: {application/json: Foo}\n')
        assert flat(merged) == {'body': 'string'}

    def test_a_missing_side_yields_the_other_by_identity(self):
        node = tree('a: 1\n')
        assert merge_structural(node, None, SCOPE) is node
        assert merge_structural(None, node, SCOPE) is node
        assert merge_structural(None, None, SCOPE) is None


class TestOpaqueDataFacets:
    """`example`, `examples` and `default` hold user data, not RAML structure."""

    def test_an_example_is_replaced_wholesale_not_merged(self):
        merged = merge('example: {name: mine}\n', 'example: {name: theirs, extra: 1}\n')
        assert flat(merged) == {'example': {'name': 'mine'}}, 'no key-by-key merge of user data'

    def test_a_key_named_example_inside_example_data_is_not_reinterpreted(self):
        merged = merge('example: {example: mine}\n', 'example: {example: theirs}\n')
        assert flat(merged) == {'example': {'example': 'mine'}}

    def test_examples_and_default_are_opaque_too(self):
        merged = merge(
            'examples: {a: {v: 1}}\ndefault: {v: 1}\n', 'examples: {a: {v: 2}, b: {v: 3}}\ndefault: {v: 2}\n'
        )
        assert flat(merged) == {'examples': {'a': {'v': '1'}}, 'default': {'v': '1'}}

    def test_a_source_only_example_is_still_grafted(self):
        # Opaqueness is about recursion, not about rule 2.
        assert flat(merge('a: 1\n', 'example: {v: 1}\n')) == {'a': '1', 'example': {'v': '1'}}


class TestPurityAndIdentity:
    """Invariants I9 and I10: no mutation, and child pointers are reused."""

    def test_neither_input_is_mutated(self):
        target, source = tree('a: {x: 1}\nenum: [mac]\n'), tree('a: {y: 2}\nenum: [win]\nb: 3\n')
        before = snapshot(target), snapshot(source)
        merge_structural(target, source, SCOPE)
        assert (snapshot(target), snapshot(source)) == before

    def test_the_result_is_a_new_container(self):
        target, source = tree('a: 1\n'), tree('b: 2\n')
        merged = merge_structural(target, source, SCOPE)
        assert merged is not target
        assert merged.content is not target.content

    def test_a_grafted_subtree_keeps_its_identity(self):
        # The provenance overlay is keyed by identity; a copied subtree would
        # silently lose every mark recorded against it.
        target, source = tree('a: 1\n'), tree('b: {deep: {deeper: 1}}\n')
        grafted = source.content[1]
        merged = merge_structural(target, source, SCOPE)
        assert merged.content[3] is grafted

    def test_an_untouched_target_value_keeps_its_identity(self):
        target, source = tree('a: {x: 1}\n'), tree('b: 2\n')
        kept = target.content[1]
        assert merge_structural(target, source, SCOPE).content[1] is kept

    def test_positions_come_from_the_target(self):
        merged = merge('a: 1\n', 'b: 2\n')
        assert (merged.line, merged.column) == (1, 1)


class TestSequenceEquality:
    def test_scalars_compare_by_tag_and_value(self):
        assert node_value_equal(tree('a: 1\n').content[1], tree('a: 1\n').content[1])
        # `1` is an int and `'1'` a string: not the same enum member.
        assert not node_value_equal(tree('a: 1\n').content[1], tree("a: '1'\n").content[1])

    def test_composites_compare_element_wise_and_in_order(self):
        assert node_value_equal(tree('a: [{x: 1}]\n').content[1], tree('a: [{x: 1}]\n').content[1])
        assert not node_value_equal(tree('a: [1, 2]\n').content[1], tree('a: [2, 1]\n').content[1])

    def test_two_applications_of_one_trait_with_different_parameters_both_survive(self):
        # docs/08 section 4.2: they are not structurally equal, so the sequence
        # merge keeps both. Deduplication by *name* happens later.
        merged = merge('is: [{secured: {tokenName: token}}]\n', 'is: [{secured: {tokenName: access_token}}]\n')
        assert len(merged.content[1].content) == 2

    def test_a_structurally_equal_item_is_dropped(self):
        merged = merge('is: [{secured: {tokenName: token}}]\n', 'is: [{secured: {tokenName: token}}]\n')
        assert len(merged.content[1].content) == 1


class TestProvenance:
    def test_a_grafted_subtree_is_marked_whole(self):
        overlay: dict = {}
        source = tree('b: {deep: {deeper: 1}}\n')
        merge_structural(tree('a: 1\n'), source, SCOPE, overlay)
        grafted = source.content[1]
        assert grafted in overlay
        assert grafted.content[1].content[1] in overlay, 'a descendant of a graft is marked too'

    def test_a_target_only_node_is_left_unmarked(self):
        overlay: dict = {}
        target = tree('a: {x: 1}\n')
        merge_structural(target, tree('b: 2\n'), SCOPE, overlay)
        assert target.content[1] not in overlay

    def test_an_existing_mark_is_not_overwritten_and_stops_the_walk(self):
        # A caller-substituted value spliced inside a trait body keeps the
        # caller's namespace even though it now sits inside a graft.
        caller, trait = ParseCtx(), ParseCtx()
        source = tree('b: {type: Substituted}\n')
        spliced = source.content[1]
        overlay = {spliced: caller}
        merge_structural(tree('a: 1\n'), source, trait, overlay)
        assert overlay[spliced] is caller
        assert spliced.content[1] not in overlay, 'the walk does not descend beneath a marked node'

    def test_a_grafted_sequence_item_is_marked(self):
        overlay: dict = {}
        source = tree('enum: [win]\n')
        item = source.content[1].content[0]
        merge_structural(tree('enum: [mac]\n'), source, SCOPE, overlay)
        assert item in overlay

    def test_no_overlay_means_no_marking(self):
        # The pure structural merge, used wherever provenance is irrelevant.
        assert merge('a: 1\n', 'b: 2\n', None) is not None


class TestCopyOverlay:
    def test_an_existing_mark_wins(self):
        first, second = ParseCtx(), ParseCtx()
        node = tree('a: 1\n')
        destination = {node: first}
        copy_overlay(destination, {node: second})
        assert destination[node] is first

    def test_an_absent_mark_is_taken(self):
        node = tree('a: 1\n')
        destination: dict = {}
        copy_overlay(destination, {node: SCOPE})
        assert destination[node] is SCOPE


class TestMarkGraftGuards:
    def test_a_missing_overlay_or_node_is_a_no_op(self):
        mark_graft(None, tree('a: 1\n'), SCOPE)
        overlay: dict = {}
        mark_graft(overlay, None, SCOPE)
        mark_graft(overlay, tree('a: 1\n'), None)
        assert overlay == {}
