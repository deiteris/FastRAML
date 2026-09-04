"""The merge: what a subtype may and may not do to its parent's constraints.

See docs/07-resolution-and-inheritance.md sections 3.4 to 3.6. Every row of
section 3.5's table gets a test that names the rule it protects. Unwrap is not
involved here — `inherit` is called directly on two resolved shapes, which is
what P9 will do once per inheritance edge.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from pyraml import RamlError
from pyraml.parser.entry import parse_from_path
from pyraml.types.complex_ import UnionShape
from pyraml.types.inherit import alias_to, inherit

LIB = '#%RAML 1.0 Library\n'


def shapes(workspace, body: str, extra: dict[str, str] | None = None):
    files = {'lib.raml': LIB + 'types:\n' + body}
    files.update(extra or {})
    root = workspace(files)
    raml = parse_from_path(root / 'lib.raml')
    return raml.types_in(raml.location)


def merge(workspace, child: str, parent: str, extra: str = ''):
    """Merge a declared `Parent` into a declared `Child` and return the child.

    Both are written as independent declarations rather than as a real
    inheritance edge, so the test controls exactly what is merged into what.
    """
    declared = shapes(workspace, f'  Child:\n{child}  Parent:\n{parent}{extra}')
    return inherit(declared['Child'], declared['Parent'])


def failure(workspace, child: str, parent: str) -> list[str]:
    with pytest.raises(RamlError) as caught:
        merge(workspace, child, parent)
    return [trace.message for chain in caught.value.chains() for trace in chain]


class TestKindCheck:
    def test_a_different_kind_is_rejected(self, workspace):
        messages = failure(workspace, '    type: string\n', '    type: integer\n')
        assert 'cannot inherit from different type' in messages

    def test_the_message_names_both_kinds(self, workspace):
        with pytest.raises(RamlError) as caught:
            merge(workspace, '    type: string\n', '    type: integer\n')
        trace = next(t for c in caught.value.chains() for t in c if t.info)
        assert trace.info == {'source': 'integer', 'target': 'string'}

    def test_an_unresolved_shape_cannot_be_inherited_from(self, workspace):
        # Reaching this means P7 was skipped, which is a bug rather than a
        # diagnostic about the document.
        declared = shapes(workspace, '  Child:\n    type: string\n')
        from pyraml.types.complex_ import UnknownShape

        parent = declared['Child'].clone_detached()
        parent.shape = UnknownShape(parent)
        with pytest.raises(RamlError) as caught:
            inherit(declared['Child'], parent)
        assert 'cannot inherit from an unresolved type' in [t.message for c in caught.value.chains() for t in c]


class TestBaseFacets:
    """docs/07 § 3.5 — the three that live on the base, before kind dispatch."""

    def test_a_description_is_inherited_when_absent(self, workspace):
        child = merge(workspace, '    type: string\n', '    type: string\n    description: from the parent\n')
        assert child.description.value == 'from the parent'

    def test_the_childs_own_description_wins(self, workspace):
        child = merge(
            workspace,
            '    type: string\n    description: mine\n',
            '    type: string\n    description: theirs\n',
        )
        assert child.description.value == 'mine'

    def test_custom_facets_union_with_the_child_winning(self, workspace):
        child = merge(
            workspace,
            '    type: string\n    facets:\n      a?: string\n      b?: string\n    a: child\n',
            '    type: string\n    facets:\n      a?: string\n      b?: string\n    a: parent\n    b: parent\n',
        )
        assert child.custom_facets['a'].raw == 'child'
        assert child.custom_facets['b'].raw == 'parent'

    def test_an_enum_is_inherited_when_absent(self, workspace):
        child = merge(workspace, '    type: string\n', '    type: string\n    enum: [a, b]\n')
        assert [node.raw for node in child.enum] == ['a', 'b']

    def test_an_enum_may_narrow(self, workspace):
        child = merge(workspace, '    type: string\n    enum: [a]\n', '    type: string\n    enum: [a, b]\n')
        assert [node.raw for node in child.enum] == ['a']

    def test_an_enum_may_not_widen(self, workspace):
        messages = failure(workspace, '    type: string\n    enum: [a, c]\n', '    type: string\n    enum: [a, b]\n')
        assert 'enum constraint violation' in messages


class TestStringRules:
    def test_min_length_may_be_raised(self, workspace):
        child = merge(workspace, '    type: string\n    minLength: 5\n', '    type: string\n    minLength: 2\n')
        assert child.shape.min_length.value == 5

    def test_min_length_may_not_be_lowered(self, workspace):
        assert 'minLength constraint violation' in failure(
            workspace, '    type: string\n    minLength: 1\n', '    type: string\n    minLength: 2\n'
        )

    def test_max_length_may_be_lowered(self, workspace):
        child = merge(workspace, '    type: string\n    maxLength: 5\n', '    type: string\n    maxLength: 9\n')
        assert child.shape.max_length.value == 5

    def test_max_length_may_not_be_raised(self, workspace):
        assert 'maxLength constraint violation' in failure(
            workspace, '    type: string\n    maxLength: 9\n', '    type: string\n    maxLength: 5\n'
        )

    def test_an_absent_bound_is_taken_from_the_parent(self, workspace):
        child = merge(workspace, '    type: string\n', '    type: string\n    minLength: 2\n    maxLength: 9\n')
        assert (child.shape.min_length.value, child.shape.max_length.value) == (2, 9)

    def test_the_childs_pattern_wins(self, workspace):
        child = merge(workspace, "    type: string\n    pattern: '^a'\n", "    type: string\n    pattern: '^b'\n")
        assert child.shape.pattern.value.pattern == '^a'

    def test_the_parents_pattern_fills_a_gap(self, workspace):
        child = merge(workspace, '    type: string\n', "    type: string\n    pattern: '^b'\n")
        assert child.shape.pattern.value.pattern == '^b'


class TestNumericRules:
    def test_minimum_may_be_raised_not_lowered(self, workspace):
        child = merge(workspace, '    type: integer\n    minimum: 5\n', '    type: integer\n    minimum: 2\n')
        assert child.shape.minimum.value == 5
        assert 'minimum constraint violation' in failure(
            workspace, '    type: integer\n    minimum: 1\n', '    type: integer\n    minimum: 2\n'
        )

    def test_maximum_may_be_lowered_not_raised(self, workspace):
        child = merge(workspace, '    type: number\n    maximum: 5\n', '    type: number\n    maximum: 9\n')
        assert child.shape.maximum.value == 5
        assert 'maximum constraint violation' in failure(
            workspace, '    type: number\n    maximum: 9\n', '    type: number\n    maximum: 5\n'
        )

    def test_multiple_of_must_be_an_integer_multiple_of_the_parents(self, workspace):
        # 6 is a multiple of 3, so every value the child admits the parent also
        # admits.
        child = merge(workspace, '    type: integer\n    multipleOf: 6\n', '    type: integer\n    multipleOf: 3\n')
        assert child.shape.multiple_of.value == 6

    def test_multiple_of_may_not_admit_values_the_parent_rejects(self, workspace):
        assert 'multipleOf constraint violation' in failure(
            workspace, '    type: integer\n    multipleOf: 4\n', '    type: integer\n    multipleOf: 3\n'
        )

    def test_a_fractional_multiple_of_is_exact(self, workspace):
        # The bound never passes through float: Fraction('0.3') / Fraction('0.1')
        # is exactly 3, where 0.3 / 0.1 in binary floating point is not.
        child = merge(workspace, '    type: number\n    multipleOf: 0.3\n', '    type: number\n    multipleOf: 0.1\n')
        # Compared against a Fraction, not against 0.3: the float 0.3 is not
        # exactly 3/10, which is the whole reason these never pass through one.
        assert child.shape.multiple_of.value == Fraction('0.3')

    def test_an_integer_format_must_match_by_width(self, workspace):
        assert 'format constraint violation' in failure(
            workspace, '    type: integer\n    format: int16\n', '    type: integer\n    format: int32\n'
        )

    def test_int_and_int32_name_one_width(self, workspace):
        # `int` is an alias for `int32` and `long` for `int64` (docs/05 § 3).
        child = merge(workspace, '    type: integer\n    format: int\n', '    type: integer\n    format: int32\n')
        assert child.shape.format.value == 'int'

    def test_a_number_format_must_match_exactly(self, workspace):
        assert 'format constraint violation' in failure(
            workspace, '    type: number\n    format: float\n', '    type: number\n    format: double\n'
        )


class TestDateTimeRules:
    def test_the_format_must_match(self, workspace):
        assert 'format constraint violation' in failure(
            workspace,
            '    type: datetime\n    format: rfc2616\n',
            '    type: datetime\n    format: rfc3339\n',
        )

    def test_an_absent_format_is_inherited(self, workspace):
        child = merge(workspace, '    type: datetime\n', '    type: datetime\n    format: rfc2616\n')
        assert child.shape.format.value == 'rfc2616'


class TestArrayRules:
    def test_items_merge_recursively(self, workspace):
        child = merge(
            workspace,
            '    type: array\n    items:\n      type: string\n      minLength: 5\n',
            '    type: array\n    items:\n      type: string\n      minLength: 2\n      maxLength: 9\n',
        )
        assert child.shape.items.shape.min_length.value == 5
        assert child.shape.items.shape.max_length.value == 9

    def test_a_violation_inside_items_propagates(self, workspace):
        assert 'minLength constraint violation' in failure(
            workspace,
            '    type: array\n    items:\n      type: string\n      minLength: 1\n',
            '    type: array\n    items:\n      type: string\n      minLength: 2\n',
        )

    def test_min_items_may_be_raised_not_lowered(self, workspace):
        child = merge(workspace, '    type: array\n    minItems: 5\n', '    type: array\n    minItems: 2\n')
        assert child.shape.min_items.value == 5
        assert 'minItems constraint violation' in failure(
            workspace, '    type: array\n    minItems: 1\n', '    type: array\n    minItems: 2\n'
        )

    def test_max_items_may_be_lowered_not_raised(self, workspace):
        child = merge(workspace, '    type: array\n    maxItems: 5\n', '    type: array\n    maxItems: 9\n')
        assert child.shape.max_items.value == 5
        assert 'maxItems constraint violation' in failure(
            workspace, '    type: array\n    maxItems: 9\n', '    type: array\n    maxItems: 5\n'
        )

    def test_a_unique_parent_requires_a_unique_child(self, workspace):
        assert 'uniqueItems constraint violation' in failure(
            workspace,
            '    type: array\n    uniqueItems: false\n',
            '    type: array\n    uniqueItems: true\n',
        )

    def test_a_child_may_add_uniqueness(self, workspace):
        child = merge(
            workspace,
            '    type: array\n    uniqueItems: true\n',
            '    type: array\n    uniqueItems: false\n',
        )
        assert child.shape.unique_items.value is True


class TestObjectRules:
    def test_new_property_names_are_added(self, workspace):
        child = merge(
            workspace,
            '    properties:\n      a: string\n',
            '    properties:\n      b: string\n',
        )
        assert sorted(child.shape.properties) == ['a', 'b']

    def test_a_shared_property_merges_recursively(self, workspace):
        child = merge(
            workspace,
            '    properties:\n      a:\n        type: string\n        minLength: 5\n',
            '    properties:\n      a:\n        type: string\n        maxLength: 9\n',
        )
        shared = child.shape.properties['a'].base.shape
        assert (shared.min_length.value, shared.max_length.value) == (5, 9)

    def test_a_required_property_may_not_become_optional(self, workspace):
        assert 'cannot make a required property optional' in failure(
            workspace,
            '    properties:\n      a?: string\n',
            '    properties:\n      a: string\n',
        )

    def test_an_optional_property_may_become_required(self, workspace):
        child = merge(
            workspace,
            '    properties:\n      a: string\n',
            '    properties:\n      a?: string\n',
        )
        assert child.shape.properties['a'].required is True

    def test_pattern_properties_merge_by_pattern(self, workspace):
        child = merge(
            workspace,
            '    properties:\n      /^a/:\n        type: string\n        minLength: 5\n',
            '    properties:\n      /^a/:\n        type: string\n        maxLength: 9\n      /^b/: string\n',
        )
        assert sorted(child.shape.pattern_properties) == ['^a', '^b']
        shared = child.shape.pattern_properties['^a'].base.shape
        assert (shared.min_length.value, shared.max_length.value) == (5, 9)

    def test_min_and_max_properties_narrow(self, workspace):
        child = merge(
            workspace,
            '    type: object\n    minProperties: 5\n    maxProperties: 6\n',
            '    type: object\n    minProperties: 2\n    maxProperties: 9\n',
        )
        assert (child.shape.min_properties.value, child.shape.max_properties.value) == (5, 6)
        assert 'minProperties constraint violation' in failure(
            workspace, '    type: object\n    minProperties: 1\n', '    type: object\n    minProperties: 2\n'
        )

    def test_additional_properties_and_discriminator_are_inherited_when_absent(self, workspace):
        child = merge(
            workspace,
            '    properties:\n      kind: string\n',
            '    additionalProperties: false\n    discriminator: kind\n    properties:\n      kind: string\n',
        )
        assert child.shape.additional_properties.value is False
        assert child.shape.discriminator.value == 'kind'


class TestFileRules:
    def test_file_types_may_narrow(self, workspace):
        child = merge(
            workspace,
            "    type: file\n    fileTypes: ['image/png']\n",
            "    type: file\n    fileTypes: ['image/png', 'image/jpeg']\n",
        )
        assert [facet.value for facet in child.shape.file_types] == ['image/png']

    def test_file_types_may_not_widen(self, workspace):
        assert 'fileTypes constraint violation' in failure(
            workspace,
            "    type: file\n    fileTypes: ['image/png', 'image/gif']\n",
            "    type: file\n    fileTypes: ['image/png']\n",
        )


class TestAnyAbsorbs:
    def test_a_parent_of_type_any_constrains_nothing(self, workspace):
        # Not even the kind check: `any` is the one source that never fails.
        child = merge(workspace, '    type: string\n    minLength: 3\n', '    type: any\n')
        assert child.type == 'string'
        assert child.shape.min_length.value == 3


class TestUnionRules:
    """docs/07 § 3.4 — the combinatorics that break naive implementations."""

    def test_a_source_union_keeps_only_compatible_members(self, workspace):
        # string narrows to the string member; the integer member is not a
        # candidate at all, so one survivor remains and the target becomes it.
        child = merge(workspace, '    type: string\n    minLength: 3\n', '    type: string | integer\n')
        assert child.type == 'string'
        assert child.shape.min_length.value == 3

    def test_several_survivors_make_the_target_a_union(self, workspace):
        declared = shapes(
            workspace,
            '  Child:\n    type: string\n  Parent:\n    type: string | string\n',
        )
        merged = inherit(declared['Child'], declared['Parent'])
        assert isinstance(merged.shape, UnionShape)
        assert len(merged.shape.any_of) == 2

    def test_no_compatible_member_is_an_error(self, workspace):
        messages = failure(workspace, '    type: string\n', '    type: integer | boolean\n')
        assert 'failed to find compatible union member' in messages

    def test_an_any_member_makes_the_whole_union_permissive(self, workspace):
        child = merge(workspace, '    type: string\n    minLength: 3\n', '    type: string | any\n')
        assert child.type == 'string'

    def test_a_target_union_narrows_every_member(self, workspace):
        child = merge(workspace, '    type: string | string\n', '    type: string\n    minLength: 2\n')
        assert all(member.shape.min_length.value == 2 for member in child.shape.any_of)

    def test_a_target_union_member_that_cannot_narrow_fails_the_merge(self, workspace):
        assert 'cannot inherit from different type' in failure(
            workspace, '    type: string | integer\n', '    type: string\n'
        )

    def test_the_targets_example_stays_on_the_union_and_not_on_its_members(self, workspace):
        """An example describes the union, and satisfies *one* member.

        Each survivor is a clone of the target and so arrives carrying its
        example. Left there, P10 would require every example to satisfy every
        member — which is the opposite of what a union means, and reads as two
        contradictory "missing required properties" errors on a correct
        document.
        """
        declared = shapes(
            workspace,
            '  Child:\n    type: string\n    example: hi\n  Parent:\n    type: string | string\n',
        )
        merged = inherit(declared['Child'], declared['Parent'])
        assert merged.example is not None
        assert all(member.example is None for member in merged.shape.any_of)

    def test_a_single_survivor_keeps_the_example(self, workspace):
        # It *becomes* the target, so it has to carry what the target declared.
        child = merge(workspace, '    type: string\n    example: hi\n', '    type: string | integer\n')
        assert child.example is not None

    def test_merging_a_union_does_not_mutate_the_declared_members(self, workspace):
        declared = shapes(
            workspace,
            '  Child:\n    type: string\n    minLength: 3\n  Parent:\n    type: string | integer\n',
        )
        parent_member = declared['Parent'].shape.any_of[0]
        inherit(declared['Child'], declared['Parent'])
        assert parent_member.shape.min_length is None, 'the survivor must be a copy'


class TestAliasing:
    """docs/07 § 3.6 — an alias takes facets wholesale and keeps its identity."""

    def test_facets_are_taken_wholesale(self, workspace):
        declared = shapes(workspace, '  Named:\n    type: string\n  Source:\n    type: string\n    minLength: 4\n')
        aliased = alias_to(declared['Named'], declared['Source'])
        assert aliased.shape.min_length.value == 4

    def test_the_target_keeps_its_own_identity(self, workspace):
        declared = shapes(workspace, '  Named:\n    type: string\n  Source:\n    type: string\n    minLength: 4\n')
        named, source = declared['Named'], declared['Source']
        identity = (named.name, named.id, named.location, named.key_pos)
        alias_to(named, source)
        assert (named.name, named.id, named.location, named.key_pos) == identity
        assert named.name != source.name

    def test_the_common_facets_are_adopted(self, workspace):
        declared = shapes(
            workspace,
            '  Named:\n    type: string\n'
            '  Source:\n    type: string\n    description: d\n    displayName: n\n    default: x\n',
        )
        aliased = alias_to(declared['Named'], declared['Source'])
        assert aliased.description.value == 'd'
        assert aliased.display_name.value == 'n'
        assert aliased.default.raw == 'x'

    def test_aliasing_a_different_kind_is_rejected(self, workspace):
        declared = shapes(workspace, '  Named:\n    type: string\n  Source:\n    type: integer\n')
        with pytest.raises(RamlError) as caught:
            alias_to(declared['Named'], declared['Source'])
        assert 'cannot alias a different type' in [t.message for c in caught.value.chains() for t in c]


class TestCycleGuard:
    def test_a_loop_in_the_chain_returns_rather_than_recursing(self, workspace):
        # Unlike resolution, a loop here is not an error: recursion is marked
        # after unwrap, not rejected during it (docs/07 § 4).
        declared = shapes(workspace, '  Node:\n    properties:\n      next: Node\n')
        node = declared['Node']
        assert inherit(node, node) is node
