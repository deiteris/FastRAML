"""P9: flattening an inheritance chain, and closing the cycles afterwards.

See docs/07-resolution-and-inheritance.md sections 3.1 to 3.3 and section 4.
`test_inherit.py` covers one merge in isolation; this covers the walk that
decides what gets merged into what, and what the model looks like afterwards.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pyraml import ParseOptions, RamlError, parse_from_path
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape

LIB = '#%RAML 1.0 Library\n'
UNWRAP = ParseOptions(unwrap=True)


def unwrapped(workspace, body: str, extra: dict[str, str] | None = None):
    """Parse a library with `unwrap=True` and return its declared types."""
    files = {'lib.raml': LIB + 'types:\n' + body}
    files.update(extra or {})
    root = workspace(files)
    raml = parse_from_path(root / 'lib.raml', UNWRAP)
    return raml, raml.types_in(raml.location)


def failure(workspace, body: str) -> list[str]:
    with pytest.raises(RamlError) as caught:
        unwrapped(workspace, body)
    return [trace.message for chain in caught.value.chains() for trace in chain]


class TestSingleInheritance:
    def test_a_child_gains_its_parents_facets(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Parent:\n    type: string\n    minLength: 2\n    maxLength: 9\n'
            '  Child:\n    type: Parent\n    minLength: 4\n',
        )
        child = types['Child'].shape
        assert (child.min_length.value, child.max_length.value) == (4, 9)

    def test_a_child_gains_its_parents_properties(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Parent:\n    properties:\n      a: string\n'
            '  Child:\n    type: Parent\n    properties:\n      b: string\n',
        )
        assert sorted(types['Child'].shape.properties) == ['a', 'b']

    def test_a_chain_of_three_flattens_completely(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  A:\n    properties:\n      a: string\n'
            '  B:\n    type: A\n    properties:\n      b: string\n'
            '  C:\n    type: B\n    properties:\n      c: string\n',
        )
        assert sorted(types['C'].shape.properties) == ['a', 'b', 'c']

    def test_a_violation_is_reported_rather_than_flattened(self, workspace):
        assert 'minLength constraint violation' in failure(
            workspace,
            '  Parent:\n    type: string\n    minLength: 4\n  Child:\n    type: Parent\n    minLength: 1\n',
        )


class TestParentIsNotMutated:
    """docs/07 § 3.3 — the corruption the synthetic shape exists to prevent."""

    def test_two_children_do_not_corrupt_their_shared_parent(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Parent:\n    properties:\n      shared: string\n'
            '  First:\n    type: Parent\n    properties:\n      a: string\n'
            '  Second:\n    type: Parent\n    properties:\n      b: string\n',
        )
        assert sorted(types['Parent'].shape.properties) == ['shared'], (
            'the parent must not have gained either child property'
        )
        assert sorted(types['First'].shape.properties) == ['a', 'shared']
        assert sorted(types['Second'].shape.properties) == ['b', 'shared']

    def test_multiple_inheritance_does_not_corrupt_the_first_parent(self, workspace):
        # The specific bug: merging parents directly into the child aliases the
        # first parent's dict, and the second merge then writes through it.
        _raml, types = unwrapped(
            workspace,
            '  Left:\n    properties:\n      l: string\n'
            '  Right:\n    properties:\n      r: string\n'
            '  Both:\n    type: [Left, Right]\n',
        )
        assert sorted(types['Left'].shape.properties) == ['l'], 'Left must not have gained r'
        assert sorted(types['Right'].shape.properties) == ['r']
        assert sorted(types['Both'].shape.properties) == ['l', 'r']

    def test_the_parents_property_objects_are_not_shared_into_a_child(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Parent:\n    properties:\n      shared:\n        type: string\n'
            '  First:\n    type: Parent\n    properties:\n      shared:\n        type: string\n        minLength: 2\n'
            '  Second:\n    type: Parent\n',
        )
        assert types['First'].shape.properties['shared'].base.shape.min_length.value == 2
        assert types['Parent'].shape.properties['shared'].base.shape.min_length is None


class TestMultipleInheritance:
    def test_facets_from_every_parent_are_folded_in(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Lower:\n    type: string\n    minLength: 2\n'
            '  Upper:\n    type: string\n    maxLength: 9\n'
            '  Both:\n    type: [Lower, Upper]\n',
        )
        both = types['Both'].shape
        assert (both.min_length.value, both.max_length.value) == (2, 9)

    def test_incompatible_parent_kinds_are_rejected(self, workspace):
        # Spec § Multiple Inheritance forbids inheriting from different
        # primitive kinds; it falls out of the merge's kind check.
        assert 'cannot inherit from different type' in failure(
            workspace,
            '  A:\n    type: string\n  B:\n    type: integer\n  Both:\n    type: [A, B]\n',
        )

    def test_an_array_synthetic_gets_its_own_items(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Short:\n    type: string[]\n    minItems: 1\n'
            '  Long:\n    type: string[]\n    maxItems: 9\n'
            '  Both:\n    type: [Short, Long]\n',
        )
        both = types['Both'].shape
        assert isinstance(both, ArrayShape)
        assert (both.min_items.value, both.max_items.value) == (1, 9)
        assert both.items.type == 'string'


class TestAliases:
    """docs/07 § 3.6 — a second name for one type: own identity, shared content."""

    def test_an_alias_takes_the_referents_facets(self, workspace):
        _raml, types = unwrapped(workspace, '  Source:\n    type: string\n    minLength: 4\n  Named: Source\n')
        assert types['Named'].shape.min_length.value == 4

    def test_an_alias_keeps_its_own_identity(self, workspace):
        _raml, types = unwrapped(workspace, '  Source:\n    type: string\n    minLength: 4\n  Named: Source\n')
        named = types['Named']
        assert named is not types['Source']
        assert named.name == 'Named'
        assert named.key_pos.line == 6, 'positioned where `Named:` was written'

    def test_the_contents_are_shared_rather_than_copied(self, workspace):
        # The alias names one type twice, so the two must not be able to drift.
        # A copy here would pass every other test in this class and still be
        # wrong the moment anything touched the referent.
        _raml, types = unwrapped(workspace, '  Source:\n    properties:\n      a: string\n  Named: Source\n')
        assert types['Named'].shape.properties is types['Source'].shape.properties

    def test_a_change_to_the_referent_shows_through_the_alias(self, workspace):
        _raml, types = unwrapped(workspace, '  Source:\n    properties:\n      a: string\n  Named: Source\n')
        del types['Source'].shape.properties['a']
        assert types['Named'].shape.properties == {}

    def test_two_aliases_of_one_type_share_with_each_other(self, workspace):
        _raml, types = unwrapped(workspace, '  Source:\n    properties:\n      a: string\n  X: Source\n  Y: Source\n')
        assert types['X'].shape.properties is types['Y'].shape.properties
        assert types['X'] is not types['Y']


class TestLinks:
    def test_a_link_becomes_inheritance_and_is_cleared(self, workspace):
        # Invariant I6's second half: `link is None` everywhere after P9.
        _raml, types = unwrapped(
            workspace,
            '  T:\n    type: !include dt.raml\n    properties:\n      b: string\n',
            {'dt.raml': '#%RAML 1.0 DataType\nproperties:\n  a: string\n'},
        )
        assert types['T'].link is None
        assert sorted(types['T'].shape.properties) == ['a', 'b']


class TestUnionCollapse:
    def test_a_child_of_a_union_is_itself_a_union(self, workspace):
        # Not docs/07 § 3.4's "source is a union, target is not": P7 gives the
        # child the referent's kind, so both sides are unions here. `minLength`
        # is not a union facet and survives only as a custom facet value.
        raml, types = unwrapped(
            workspace,
            '  Either:\n    type: string | integer\n  Narrowed:\n    type: Either\n    minLength: 2\n',
        )
        narrowed = types['Narrowed']
        assert isinstance(narrowed.shape, UnionShape)
        assert [member.type for member in narrowed.shape.any_of] == ['string', 'integer']
        # The name index must point at whatever the merge returned.
        assert raml.types_in(raml.location)['Narrowed'] is narrowed

    def test_a_child_with_no_members_of_its_own_adopts_the_parents(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  Either:\n    type: integer | number\n  Bounded:\n    type: Either\n    maximum: 2\n    minimum: 1\n',
        )
        assert isinstance(types['Bounded'].shape, UnionShape)
        assert [member.type for member in types['Bounded'].shape.any_of] == ['integer', 'number']

    def test_an_incompatible_member_set_is_reported(self, workspace):
        # Both sides are unions and the child declares members of its own, so
        # each of the parent's has to find a compatible one among them.
        assert 'failed to find compatible union member' in failure(
            workspace,
            '  Either:\n    type: integer | boolean\n  Narrowed:\n    type: Either\n    anyOf:\n      - string\n',
        )


class TestRecursionMarking:
    """docs/07 § 4 — where a cycle closes, and what closes it."""

    def test_a_self_referential_property_becomes_a_marker(self, workspace):
        _raml, types = unwrapped(workspace, '  Node:\n    properties:\n      next: Node\n')
        node = types['Node']
        marker = node.shape.properties['next'].base
        assert isinstance(marker.shape, RecursiveShape)
        assert marker.shape.head is node
        assert marker.type == 'recursive'

    def test_the_marker_is_not_the_head_itself(self, workspace):
        _raml, types = unwrapped(workspace, '  Node:\n    properties:\n      next: Node\n')
        node = types['Node']
        assert node.shape.properties['next'].base is not node

    def test_a_two_step_cycle_is_marked(self, workspace):
        _raml, types = unwrapped(
            workspace,
            '  A:\n    properties:\n      b: B\n  B:\n    properties:\n      a: A\n',
        )
        inner = types['A'].shape.properties['b'].base.shape.properties['a'].base
        assert isinstance(inner.shape, RecursiveShape)

    def test_a_cycle_through_an_array_is_marked(self, workspace):
        _raml, types = unwrapped(workspace, '  Node:\n    properties:\n      kids: Node[]\n')
        items = types['Node'].shape.properties['kids'].base.shape.items
        assert isinstance(items.shape, RecursiveShape)

    def test_a_diamond_is_not_a_cycle(self, workspace):
        # Two paths to one shape is not recursion; only a path back to an
        # ancestor is.
        _raml, types = unwrapped(
            workspace,
            '  Leaf:\n    type: string\n  Node:\n    properties:\n      a: Leaf\n      b: Leaf\n',
        )
        properties = types['Node'].shape.properties
        assert not isinstance(properties['a'].base.shape, RecursiveShape)
        assert not isinstance(properties['b'].base.shape, RecursiveShape)

    def test_the_model_can_be_walked_without_recursing_forever(self, workspace):
        _raml, types = unwrapped(workspace, '  Node:\n    properties:\n      next: Node\n')

        def walk(base, depth=0):
            if depth > 10:
                raise AssertionError('the graph is still cyclic')
            shape = base.shape
            if isinstance(shape, RecursiveShape):
                return 1
            if isinstance(shape, ObjectShape):
                return 1 + sum(walk(p.base, depth + 1) for p in (shape.properties or {}).values())
            return 1

        assert walk(types['Node']) == 2


class TestInvariantI6:
    def test_every_reachable_shape_is_flattened_and_unlinked(self, workspace):
        raml, _types = unwrapped(
            workspace,
            '  Parent:\n    properties:\n      a: string\n'
            '  Child:\n    type: Parent\n    properties:\n      b: string\n'
            '  Listed: Child[]\n'
            '  Node:\n    properties:\n      next: Node\n',
        )
        assert raml.is_unwrapped
        offenders = [
            f'{shape.id} ({shape.name!r})' for shape in raml.shapes if not shape._unwrapped or shape.link is not None
        ]
        assert not offenders, offenders

    def test_the_shape_index_is_rebuilt_rather_than_appended_to(self, workspace):
        # After flattening, an entry from before describes a model that no
        # longer exists — a union member may have been replaced by a merged
        # copy (docs/07 § 3.2).
        raml, _types = unwrapped(
            workspace,
            '  Either:\n    type: string | integer\n  Narrowed:\n    type: Either\n    minLength: 2\n',
        )
        assert all(shape._unwrapped for shape in raml.shapes)


class TestDepthGuard:
    def test_a_document_nested_past_the_limit_is_a_diagnostic(self, workspace):
        # docs/12 § 11: a positioned error, not a RecursionError from wherever
        # the interpreter happened to give up.
        # Genuinely nested, not a chain of sibling declarations: unwrapping
        # those costs one frame each, because every parent is already flattened
        # by the time the declaration that names it is reached.
        depth = 30
        body = '  T:\n'
        for level in range(depth):
            pad = '  ' * level
            body += f'{pad}    properties:\n{pad}      a:\n'
        body += '  ' * depth + '        type: string\n'
        root = workspace({'lib.raml': LIB + 'types:\n' + body})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True, max_type_depth=10))
        assert 'type nesting too deep' in [t.message for c in caught.value.chains() for t in c]


DECLARATIONS = [
    '  T:\n    type: string\n    minLength: 2\n',
    '  P:\n    properties:\n      a: string\n  T:\n    type: P\n    properties:\n      b: string\n',
    '  P:\n    type: string\n    maxLength: 9\n  T:\n    type: P\n    minLength: 2\n',
    '  A:\n    properties:\n      a: string\n  B:\n    properties:\n      b: string\n  T:\n    type: [A, B]\n',
    '  T:\n    properties:\n      next: T\n',
    '  T: string[]\n',
    '  T: string | integer\n',
    '  P: string\n  T: P\n',
]


@settings(max_examples=len(DECLARATIONS), deadline=None)
@given(declaration=st.sampled_from(DECLARATIONS))
def test_unwrapping_is_idempotent(tmp_path_factory, declaration):
    """`unwrap(unwrap(x))` is `unwrap(x)`.

    The property most likely to be broken by a later change: a second pass that
    merged a parent in twice would double bounds or duplicate members without
    failing anything else.
    """
    from pyraml.types.unwrap import unwrap_shapes
    from tests.unit.conftest import write_files

    root = write_files(tmp_path_factory.mktemp('idem'), {'lib.raml': LIB + 'types:\n' + declaration})
    raml = parse_from_path(root / 'lib.raml', UNWRAP)

    def describe(base, seen=None):
        seen = (seen or frozenset()) | {id(base)}
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            return f'rec({shape.head.name})'
        if id(base) in (seen - {id(base)}):
            return '<cycle>'
        if isinstance(shape, ArrayShape):
            return f'array[{describe(shape.items, seen)}]'
        if isinstance(shape, UnionShape):
            return 'union(' + ','.join(describe(m, seen) for m in shape.any_of or ()) + ')'
        if isinstance(shape, ObjectShape):
            body = ','.join(
                f'{name}{"" if p.required else "?"}:{describe(p.base, seen)}'
                for name, p in sorted((shape.properties or {}).items())
            )
            return f'object({body})'
        facets = ','.join(
            f'{name}={getattr(getattr(shape, name, None), "value", None)}'
            for name in ('min_length', 'max_length', 'minimum', 'maximum', 'min_items', 'max_items')
            if getattr(shape, name, None) is not None
        )
        return f'{base.type}({facets})'

    before = {name: describe(base) for name, base in raml.types_in(raml.location).items()}
    unwrap_shapes(raml)
    after = {name: describe(base) for name, base in raml.types_in(raml.location).items()}
    assert before == after
