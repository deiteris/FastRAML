"""Copying a shape without `copy.deepcopy`.

See docs/07-resolution-and-inheritance.md § 6. Unwrap and validation are
both defined in terms of these, and picking the wrong one is a correctness bug
in one direction and a performance bug in the other.
"""

from __future__ import annotations

import pytest

from fastraml.parser.entry import parse_from_path
from fastraml.types.complex_ import ArrayShape, ObjectShape, UnionShape

LIB = '#%RAML 1.0 Library\n'


def library(workspace, body: str, extra: dict[str, str] | None = None):
    files = {'lib.raml': LIB + 'types:\n' + body}
    files.update(extra or {})
    root = workspace(files)
    raml = parse_from_path(root / 'lib.raml')
    return raml.types_in(raml.location)


CYCLE = '  Node:\n    properties:\n      name: string\n      next: Node\n'


class TestStructurePreserved:
    def test_a_cycle_stays_a_cycle_rather_than_recursing(self, workspace):
        # `Node.next: Node` is legal RAML. A copy that followed it naively would
        # hang, which is why the memo registers the clone before its children.
        node = library(workspace, CYCLE)['Node']
        clone = node.clone_detached()
        assert clone.shape.properties['next'].base.alias is clone
        assert clone.shape.properties['next'].base.alias is not node

    def test_a_diamond_stays_a_diamond_under_one_memo(self, workspace):
        types = library(workspace, CYCLE + '  Listed: Node[]\n  Wrapped:\n    type: Node\n')
        memo: dict = {}
        listed = types['Listed'].clone(memo)
        wrapped = types['Wrapped'].clone(memo)
        assert listed.shape.items.alias is wrapped.inherits[0]

    def test_separate_memos_do_not_share(self, workspace):
        types = library(workspace, CYCLE + '  Listed: Node[]\n  Wrapped:\n    type: Node\n')
        listed = types['Listed'].clone_detached()
        wrapped = types['Wrapped'].clone_detached()
        assert listed.shape.items.alias is not wrapped.inherits[0]

    def test_a_clone_keeps_the_original_id(self, workspace):
        # The memo is keyed on it. A caller needing a distinct identity assigns
        # a fresh one; union member merging is the one that does.
        node = library(workspace, CYCLE)['Node']
        assert node.clone_detached().id == node.id


class TestDetachment:
    def test_nothing_mutable_is_shared_with_the_original(self, workspace):
        node = library(workspace, CYCLE)['Node']
        clone = node.clone_detached()
        assert clone is not node
        assert clone.shape is not node.shape
        assert clone.shape.properties is not node.shape.properties
        assert clone.custom_facets is not node.custom_facets
        assert clone.annotations is not node.annotations
        assert clone.inherits is not node.inherits

    def test_mutating_a_clone_does_not_reach_the_original(self, workspace):
        node = library(workspace, CYCLE)['Node']
        clone = node.clone_detached()
        del clone.shape.properties['name']
        assert 'name' in node.shape.properties

    def test_a_child_is_a_different_object(self, workspace):
        node = library(workspace, CYCLE)['Node']
        clone = node.clone_detached()
        assert clone.shape.properties['name'].base is not node.shape.properties['name'].base


class TestSharedByDesign:
    """What a clone deliberately does *not* copy (docs/07 § 6)."""

    def test_scalar_facets_are_shared(self, workspace):
        # Nothing mutates a ScalarFacet in place; `inherit` only rebinds the
        # field. Copying them would be pure cost.
        base = library(workspace, '  T:\n    type: string\n    minLength: 3\n')['T']
        assert base.clone_detached().shape.min_length is base.shape.min_length

    def test_a_compiled_pattern_is_shared(self, workspace):
        # One of the three things `copy.deepcopy` would have copied pointlessly.
        base = library(workspace, '  T:\n    properties:\n      /^a/: string\n')['T']
        original = base.shape.pattern_properties['^a']
        assert base.clone_detached().shape.pattern_properties['^a'].pattern is original.pattern

    def test_the_registry_back_pointer_is_shared(self, workspace):
        node = library(workspace, CYCLE)['Node']
        assert node.clone_detached()._raml is node._raml


class TestPerKind:
    def test_an_array_clones_its_items(self, workspace):
        base = library(workspace, '  T: string[]\n')['T']
        clone = base.clone_detached()
        assert isinstance(clone.shape, ArrayShape)
        assert clone.shape.items is not base.shape.items
        assert clone.shape.items.type == 'string'

    def test_a_union_clones_every_member(self, workspace):
        base = library(workspace, '  T: string | integer\n')['T']
        clone = base.clone_detached()
        assert isinstance(clone.shape, UnionShape)
        assert [member.type for member in clone.shape.any_of] == ['string', 'integer']
        assert all(new is not old for new, old in zip(clone.shape.any_of, base.shape.any_of, strict=True))

    def test_an_object_clones_both_property_maps(self, workspace):
        base = library(workspace, '  T:\n    properties:\n      a: string\n      /^b/: integer\n')['T']
        clone = base.clone_detached()
        assert isinstance(clone.shape, ObjectShape)
        assert list(clone.shape.properties) == ['a']
        assert list(clone.shape.pattern_properties) == ['^b']
        assert clone.shape.pattern_properties['^b'].base is not base.shape.pattern_properties['^b'].base

    def test_a_scalar_kind_copies_its_facets_by_slot(self, workspace):
        # The generic path: no per-kind clone is written for the fourteen kinds
        # that hold no declarations.
        base = library(workspace, '  T:\n    type: integer\n    minimum: 1\n    format: int32\n')['T']
        clone = base.clone_detached()
        assert clone.shape.minimum.value == 1
        assert clone.shape.format.value == 'int32'
        assert clone.shape is not base.shape

    def test_custom_facet_declarations_are_cloned(self, workspace):
        base = library(workspace, '  T:\n    type: string\n    facets:\n      extra: integer\n')['T']
        clone = base.clone_detached()
        assert list(clone.custom_facet_defs) == ['extra']
        assert clone.custom_facet_defs['extra'].base is not base.custom_facet_defs['extra'].base


class TestLinkedShapes:
    def test_a_link_becomes_inheritance_in_the_clone(self, workspace):
        # docs/07 § 1: P9 rewrites a link to inheritance at the start of unwrap, and
        # unwrap is the only reader. Doing it here keeps one DataTypeFragment
        # per file (invariant I3) instead of copying the fragment.
        types = library(
            workspace,
            '  T:\n    type: !include dt.raml\n',
            {'dt.raml': '#%RAML 1.0 DataType\nproperties:\n  a: string\n'},
        )
        clone = types['T'].clone_detached()
        assert clone.link is None
        assert len(clone.inherits) == 1
        assert clone.inherits[0].type == 'object'

    def test_the_linked_shape_is_copied_not_shared(self, workspace):
        types = library(
            workspace,
            '  T:\n    type: !include dt.raml\n',
            {'dt.raml': '#%RAML 1.0 DataType\nproperties:\n  a: string\n'},
        )
        original = types['T'].link.shape
        assert types['T'].clone_detached().inherits[0] is not original

    def test_the_original_keeps_its_link(self, workspace):
        types = library(
            workspace,
            '  T:\n    type: !include dt.raml\n',
            {'dt.raml': '#%RAML 1.0 DataType\ntype: string\n'},
        )
        types['T'].clone_detached()
        assert types['T'].link is not None, 'cloning must not disturb the original'


class TestNoDeepcopy:
    def test_the_package_never_imports_the_copy_module(self):
        # CLAUDE.md forbids `copy.deepcopy`: it would copy the Raml
        # back-pointer, the compiled patterns and the YAML nodes. Asserted over
        # the import graph rather than by grepping, which would trip over the
        # several docstrings that name it in order to rule it out.
        import ast
        import pathlib

        offenders: list[str] = []
        for path in pathlib.Path('fastraml').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import) and any(alias.name == 'copy' for alias in node.names):
                    offenders.append(f'{path}:{node.lineno} import copy')
                elif isinstance(node, ast.ImportFrom) and node.module == 'copy':
                    offenders.append(f'{path}:{node.lineno} from copy import ...')
        assert not offenders, '\n'.join(offenders)


@pytest.mark.parametrize(
    'declaration',
    [
        '  T: string\n',
        '  T: string[]\n',
        '  T: string?\n',
        '  T: string | integer\n',
        '  T:\n    properties:\n      a: string\n',
        '  T:\n    type: string\n    minLength: 2\n',
        CYCLE.replace('Node', 'T'),
    ],
    ids=['scalar', 'array', 'optional', 'union', 'object', 'narrowed', 'recursive'],
)
def test_cloning_twice_gives_equal_structure(workspace, declaration):
    """A clone is itself cloneable, and the second copy matches the first."""
    base = library(workspace, declaration)['T']

    def describe(shape_base, seen=None):
        seen = seen or set()
        if id(shape_base) in seen:
            return '<cycle>'
        seen = seen | {id(shape_base)}
        shape = shape_base.shape
        if isinstance(shape, ArrayShape):
            return f'array[{describe(shape.items, seen)}]'
        if isinstance(shape, UnionShape):
            return 'union(' + ','.join(describe(m, seen) for m in shape.any_of) + ')'
        if isinstance(shape, ObjectShape):
            inner = ','.join(f'{k}:{describe(p.base, seen)}' for k, p in (shape.properties or {}).items())
            return f'object({inner})'
        return shape_base.type

    once = base.clone_detached()
    assert describe(once) == describe(base)
    assert describe(once.clone_detached()) == describe(base)
