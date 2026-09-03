"""P7: settling a declaration's kind, and binding the names inside it.

See docs/06-type-expressions.md section 3 and docs/07-resolution-and-inheritance.md
sections 1-2. `test_expressions.py` covers the grammar; this file covers what
the AST is turned into.
"""

from __future__ import annotations

import pytest

from pyraml import RamlError
from pyraml.parser.entry import parse_from_path
from pyraml.types.complex_ import ArrayShape, UnionShape, UnknownShape

LIB = '#%RAML 1.0 Library\n'


def library(workspace, body: str, extra: dict[str, str] | None = None):
    """Parse a one-file library whose `types:` is `body`."""
    files = {'lib.raml': LIB + 'types:\n' + body}
    files.update(extra or {})
    root = workspace(files)
    raml = parse_from_path(root / 'lib.raml')
    return raml.types_in(raml.location)


def failure(workspace, body: str, extra: dict[str, str] | None = None) -> list[str]:
    """The message chain of the first diagnostic `body` produces."""
    with pytest.raises(RamlError) as caught:
        library(workspace, body, extra)
    return [trace.message for trace in next(iter(caught.value.chains()))]


def describe(base) -> str:
    """A compact rendering of a resolved shape, for structural assertions."""
    shape = base.shape
    if isinstance(shape, ArrayShape):
        return f'array[{describe(shape.items)}]'
    if isinstance(shape, UnionShape):
        return 'union(' + ' | '.join(describe(member) for member in shape.any_of) + ')'
    return base.type


class TestInvariantI5:
    def test_no_unknown_shape_survives_a_parse(self, workspace):
        types = library(
            workspace,
            '  Person:\n    properties:\n      name: string\n'
            '  Alias: Person\n'
            '  Listed: Person[]\n'
            '  Maybe: Person?\n'
            '  Either: Person | string\n',
        )
        assert types  # the walk below is only meaningful if something resolved
        for base in types.values():
            assert not isinstance(base.shape, UnknownShape), base.name

    def test_the_worklist_is_empty_afterwards(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  A: string[]\n  B: A | nil\n'})
        assert not parse_from_path(root / 'lib.raml').unresolved_shapes


class TestExpressionStructure:
    """docs/06 section 3 — one row of the visitor's table each."""

    @pytest.mark.parametrize(
        ('expression', 'expected'),
        [
            ('string', 'string'),
            ('string[]', 'array[string]'),
            ('string[][]', 'array[array[string]]'),
            ('string?', 'union(string | nil)'),
            ('string | nil', 'union(string | nil)'),
            ('string | integer | nil', 'union(string | integer | nil)'),
            ('(string | integer)[]', 'array[union(string | integer)]'),
            ('(string)', 'string'),
        ],
    )
    def test_an_expression_builds_its_shape(self, workspace, expression, expected):
        types = library(workspace, f'  T: {expression}\n')
        assert describe(types['T']) == expected

    def test_an_optional_array_is_not_an_array_of_optionals(self, workspace):
        # A postfix notation applies to everything to its left, so the rightmost
        # is outermost (docs/06 § 3). This is the only pair that distinguishes
        # the two directions: `string[][]` nests the same either way.
        assert describe(library(workspace, '  T: string[]?\n')['T']) == 'union(array[string] | nil)'

    def test_a_union_member_is_its_own_declaration(self, workspace):
        members = library(workspace, '  T: string | integer\n')['T'].shape.any_of
        assert len({member.id for member in members}) == 2


class TestFacetIsolation:
    """docs/06 section 3 — anonymous inner shapes exist to keep facets apart."""

    def test_facets_beside_an_expression_land_on_the_outermost_shape(self, workspace):
        base = library(workspace, '  T:\n    type: string[]\n    minItems: 1\n')['T']
        assert base.shape.min_items.value == 1
        item = base.shape.items
        assert item.type == 'string'
        assert item.custom_facets == {}, 'nothing written beside the expression reaches the item'

    def test_an_item_type_does_not_inherit_the_array_declaration_facets(self, workspace):
        # `minLength` is not an array facet, so it becomes a custom facet value
        # on the array — and must not reach the string it wraps.
        base = library(workspace, '  T:\n    type: string[]\n    minLength: 3\n')['T']
        assert base.shape.items.shape.min_length is None
        assert list(base.custom_facets) == ['minLength']


class TestAliasVersusInheritance:
    """docs/06 section 3.1 — the resolution half; the decode half is in
    `test_shape_decode.py`."""

    def test_a_bare_scalar_reference_aliases(self, workspace):
        types = library(workspace, '  Person:\n    properties:\n      name: string\n  Alias: Person\n')
        assert types['Alias'].alias is types['Person']
        assert types['Alias'].inherits == []
        assert types['Alias'].type == 'object', 'an alias takes the referent kind'

    def test_a_mapping_declaration_inherits(self, workspace):
        types = library(
            workspace,
            '  Person:\n    properties:\n      name: string\n  Sub:\n    type: Person\n    minProperties: 1\n',
        )
        assert types['Sub'].inherits == [types['Person']]
        assert types['Sub'].alias is None

    def test_a_mapping_carrying_only_type_still_inherits(self, workspace):
        types = library(workspace, '  Person:\n    properties:\n      name: string\n  Sub:\n    type: Person\n')
        assert types['Sub'].inherits == [types['Person']]
        assert types['Sub'].alias is None

    def test_an_inner_reference_aliases(self, workspace):
        # `Person[]` items have no facets of their own to narrow with.
        types = library(workspace, '  Person:\n    properties:\n      name: string\n  Listed: Person[]\n')
        assert types['Listed'].shape.items.alias is types['Person']


class TestForwardAndOutOfOrder:
    def test_a_type_may_be_used_before_it_is_declared(self, workspace):
        # Resolution is deferred to P7 precisely so that this works (docs/04 § 4.3).
        types = library(workspace, '  Uses: Later\n  Later:\n    properties:\n      a: string\n')
        assert types['Uses'].type == 'object'

    def test_resolving_a_referent_early_is_not_repeated(self, workspace):
        types = library(workspace, '  A: C\n  B: C\n  C: string\n')
        assert (types['A'].type, types['B'].type) == ('string', 'string')
        assert types['A'].alias is types['C']


class TestMultipleInheritance:
    def test_a_composite_takes_the_first_parent_kind(self, workspace):
        types = library(
            workspace,
            '  Cat:\n    properties:\n      a: string\n'
            '  Dog:\n    properties:\n      b: string\n'
            '  Both:\n    type: [Cat, Dog]\n',
        )
        both = types['Both']
        assert both.type == 'object'
        # Each sequence entry is a declaration in its own right — a bare
        # reference, so it *aliases* the type it names rather than being it.
        # `Both.inherits` therefore holds two reference shapes, not the two
        # declarations. Phase 4 follows the alias when it merges.
        assert [parent.name for parent in both.inherits] == ['Cat', 'Dog']
        assert [parent.alias for parent in both.inherits] == [types['Cat'], types['Dog']]
        assert types['Cat'] not in both.inherits

    def test_an_unknown_parent_is_reported(self, workspace):
        assert 'reference not found' in failure(workspace, '  T:\n    type: [Gone, Also]\n')

    def test_an_empty_parent_sequence_is_reported(self, workspace):
        assert 'type must name at least one parent' in failure(workspace, '  T:\n    type: []\n')


class TestLinkedFragments:
    def test_an_included_data_type_supplies_the_kind(self, workspace):
        types = library(
            workspace,
            '  T:\n    type: !include dt.raml\n',
            {'dt.raml': '#%RAML 1.0 DataType\nproperties:\n  a: string\n'},
        )
        assert types['T'].type == 'object'

    def test_the_link_is_not_rewritten_to_inheritance(self, workspace):
        # That rewrite is the first step of unwrap (docs/07 § 2). Doing it here
        # would hide the indirection from a consumer that did not ask for it.
        types = library(
            workspace,
            '  T:\n    type: !include dt.raml\n',
            {'dt.raml': '#%RAML 1.0 DataType\ntype: string\n'},
        )
        assert types['T'].link is not None
        assert types['T'].inherits == []


class TestDiagnostics:
    def test_a_cycle_is_an_error_rather_than_a_hang(self, workspace):
        assert 'cyclic type reference' in failure(workspace, '  A: B\n  B: A\n')

    def test_a_cycle_through_an_array_is_still_a_cycle(self, workspace):
        assert 'cyclic type reference' in failure(workspace, '  A: B[]\n  B: A\n')

    def test_a_direct_self_reference_is_named_as_such(self, workspace):
        # Caught by identity before the cycle flag can fire.
        assert 'self-referential type' in failure(workspace, '  A: A\n')

    def test_an_unknown_name_is_reported(self, workspace):
        assert 'reference not found' in failure(workspace, '  A: Nope\n')

    def test_an_unknown_library_is_reported(self, workspace):
        assert 'library not found' in failure(workspace, '  A: nolib.Thing\n')

    def test_a_malformed_expression_is_reported(self, workspace):
        assert 'invalid type expression' in failure(workspace, '  A: Foo |\n')

    def test_a_property_cycle_is_legal(self, workspace):
        # Only cycles *through resolution* are errors; a cycle through a
        # property is marked, not rejected, and not until P9 (docs/07 § 4).
        types = library(workspace, '  Node:\n    properties:\n      next: Node\n')
        assert types['Node'].shape.properties['next'].base.alias is types['Node']


class TestErrorPositions:
    def test_a_reference_error_points_inside_the_expression(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  A: Nope\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml')
        trace = next(iter(caught.value.chains()))[-1]
        assert (trace.position.line, trace.position.column) == (3, 6), 'the column of `Nope`, not of `A`'

    def test_one_cached_parse_still_yields_two_located_diagnostics(self, workspace):
        # docs/06 § 2.3: the AST is memoised on text alone, so the location and
        # the rebased column have to come from the caller. Two files, one parse.
        root = workspace(
            {
                'lib.raml': LIB + 'uses:\n  other: other.raml\ntypes:\n  A: Foo |\n',
                'other.raml': LIB + 'types:\n  B: Foo |\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'lib.raml')
        located = {
            (trace.location.rsplit('/', 1)[-1], trace.position.line)
            for chain in caught.value.chains()
            for trace in chain
            if trace.message == 'invalid type expression'
        }
        assert located == {('lib.raml', 5), ('other.raml', 3)}


class TestTypeExprRefs:
    """docs/06 section 3.2 — one record per name, for tooling."""

    def test_a_primitive_records_its_keyword(self, workspace):
        # The outer shape of `string[]` is the array; the keyword is on the item.
        item = library(workspace, '  T: string[]\n')['T'].shape.items
        assert [(ref.builtin, ref.column) for ref in item.type_expr_refs] == [('string', 6)]

    def test_a_qualified_name_emits_the_prefix_and_the_name(self, workspace):
        types = library(
            workspace,
            '  T: models.Thing\n',
            {
                'lib.raml': LIB + 'uses:\n  models: models.raml\ntypes:\n  T: models.Thing\n',
                'models.raml': LIB + 'types:\n  Thing: string\n',
            },
        )
        refs = types['T'].type_expr_refs
        assert len(refs) == 2
        prefix, name = refs
        assert (prefix.library_alias, prefix.column) == ('models', 6)
        assert prefix.library_link is not None
        # Past `models` and the dot it is written with.
        assert (name.resolved.name, name.column) == ('Thing', 13)

    def test_an_unqualified_name_emits_one_ref(self, workspace):
        types = library(workspace, '  Thing: string\n  T: Thing\n')
        refs = types['T'].type_expr_refs
        assert len(refs) == 1
        assert refs[0].resolved is types['Thing']
        assert refs[0].library_link is None


class TestAnnotationTypes:
    def test_an_annotation_type_resolves_against_annotation_types(self, workspace):
        root = workspace(
            {
                'lib.raml': LIB + 'annotationTypes:\n  Base:\n    properties:\n      a: string\n  Derived: Base\n',
            }
        )
        raml = parse_from_path(root / 'lib.raml')
        declared = raml.annotation_types_in(raml.location)
        assert declared['Derived'].alias is declared['Base']

    def test_an_annotation_type_falls_back_to_types(self, workspace):
        # Spec § Declaring Annotation Types: the syntax is a data type's, and it
        # may extend one (docs/04 § 3.1).
        root = workspace(
            {'lib.raml': LIB + 'types:\n  Config:\n    properties:\n      a: string\nannotationTypes:\n  Use: Config\n'}
        )
        raml = parse_from_path(root / 'lib.raml')
        assert raml.annotation_types_in(raml.location)['Use'].type == 'object'
