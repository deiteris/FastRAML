"""P7: settling a declaration's kind, and binding the names inside it.

See docs/06-type-expressions.md § 3 and docs/07-resolution-and-inheritance.md
§ 2. `test_expressions.py` covers the grammar; this file covers what
the AST is turned into.
"""

from __future__ import annotations

import gc

import pytest

from fastraml import ParseOptions, RamlError
from fastraml.types.base import BaseShape
from fastraml.types.complex_ import ArrayShape, UnionShape, UnknownShape
from fastraml.yamlnode import Node, WrittenScalar

LIB = '#%RAML 1.0 Library\n'


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def library(workspace, body: str, extra: dict[str, str] | None = None):
    """Parse a one-file library whose `types:` is `body`."""
    files = {'lib.raml': LIB + 'types:\n' + body}
    files.update(extra or {})
    root = workspace(files)
    raml = workspace.parse(root / 'lib.raml')
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
        assert not workspace.parse(root / 'lib.raml').unresolved_shapes


#: go-raml's expression corpus, verbatim, paired with the shape each builds.
#: `test_expressions.py` pins the same thirteen lines at AST level; this is the
#: other half, and the corpus is kept inline there and here so neither test
#: needs the sibling checkout.
#:
#: The names they use are declared by `CORPUS_DECLARATIONS` below. Note that
#: `date-time` is not a RAML built-in — the spec's name is `datetime` — so the
#: corpus treats it as an ordinary reference, and so must this.
EXAMPLES_CORPUS = [
    ('string', 'string'),
    ('integer?', 'union(integer | nil)'),
    ('date-time[]', 'array[datetime]'),
    ('Ref?', 'union(object | nil)'),
    ('Ref[]', 'array[object]'),
    ('external.Ref', 'file'),
    ('external.Ref?', 'union(file | nil)'),
    ('external.Ref[]', 'array[file]'),
    ('string | nil', 'union(string | nil)'),
    ('Ref | string', 'union(object | string)'),
    ('(string | integer)?', 'union(union(string | integer) | nil)'),
    ('(string | integer)[]', 'array[union(string | integer)]'),
    (
        '(string | integer) | Ref[] | external.Ref?',
        'union(union(string | integer) | array[object] | union(file | nil))',
    ),
]

#: Each referenced name resolves to a different kind, so a wrong binding shows
#: up as a wrong kind rather than passing unnoticed.
CORPUS_DECLARATIONS = '  Ref:\n    properties:\n      a: string\n  date-time: datetime\n'
CORPUS_LIBRARY = {'ext.raml': LIB + 'types:\n  Ref: file\n'}


class TestExamplesCorpus:
    """Every line of the expression corpus builds its shape."""

    @pytest.mark.parametrize(('expression', 'expected'), EXAMPLES_CORPUS, ids=[line for line, _ in EXAMPLES_CORPUS])
    def test_a_corpus_line_builds_its_shape(self, workspace, expression, expected):
        root = workspace(
            {
                'lib.raml': LIB
                + 'uses:\n  external: ext.raml\n'
                + 'types:\n'
                + CORPUS_DECLARATIONS
                + f'  T: {expression}\n',
                **CORPUS_LIBRARY,
            }
        )
        raml = workspace.parse(root / 'lib.raml')
        assert describe(raml.types_in(raml.location)['T']) == expected


class TestExpressionStructure:
    """docs/06 § 3 — one row of the visitor's table each."""

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
    """docs/06 § 3 — anonymous inner shapes exist to keep facets apart."""

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
    """docs/06 § 3 — the resolution half; the decode half is in
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
        # Resolution is deferred to P7 precisely so that this works (docs/04 § 4).
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
        # declarations. P9 follows the alias when it merges.
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
        # That rewrite is the first step of unwrap (docs/07 § 1). Doing it here
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

    @pytest.mark.parametrize(
        ('document', 'span'),
        [
            pytest.param(LIB + 'types:\n  A: string | Nope\n', (3, 15, 3, 19), id='plain'),
            pytest.param(LIB + 'types:\n  A: "Nope | string"\n', (3, 7, 3, 11), id='quoted'),
            pytest.param(
                '#%RAML 1.0\ntitle: T\nresourceTypes:\n  rt:\n    get:\n      body:\n'
                '        application/json:\n          type: <<item>> | string\n'
                '/a:\n  type: {rt: {item: Nope}}\n',
                (10, 21, 10, 25),
                id='in a value the caller wrote',
            ),
        ],
    )
    def test_an_unknown_name_spans_the_name(self, workspace, document, span):
        # docs/11 § 3: an editor underlines the whole name, not its first character.
        root = workspace({'doc.raml': document})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'doc.raml')
        (chain,) = caught.value.chains()
        assert chain[-1].message == 'reference not found'
        position = chain[-1].position
        assert (position.line, position.column, position.end_line, position.end_column) == span

    def test_an_unknown_library_is_reported(self, workspace):
        assert 'library not found' in failure(workspace, '  A: nolib.Thing\n')

    def test_a_malformed_expression_is_reported(self, workspace):
        assert 'invalid type expression' in failure(workspace, '  A: Foo |\n')

    def test_a_property_cycle_is_legal(self, workspace):
        # Only cycles *through resolution* are errors; a cycle through a
        # property is marked, not rejected, and not until P9 (docs/07 § 6).
        types = library(workspace, '  Node:\n    properties:\n      next: Node\n')
        assert types['Node'].shape.properties['next'].base.alias is types['Node']


class TestErrorPositions:
    def test_a_reference_error_points_inside_the_expression(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  A: Nope\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml')
        trace = next(iter(caught.value.chains()))[-1]
        assert (trace.position.line, trace.position.column) == (3, 6), 'the column of `Nope`, not of `A`'

    def test_an_error_in_a_quoted_expression_points_past_the_quote(self, workspace):
        root = workspace({'lib.raml': LIB + 'types:\n  A: "Nope"\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml')
        trace = next(iter(caught.value.chains()))[-1]
        assert (trace.position.line, trace.position.column) == (3, 7), 'the column of `Nope`, not of the quote'

    def test_one_cached_parse_still_yields_two_located_diagnostics(self, workspace):
        # docs/06 § 2: the AST is memoised on text alone, so the location and
        # the rebased column have to come from the caller. Two files, one parse.
        root = workspace(
            {
                'lib.raml': LIB + 'uses:\n  other: other.raml\ntypes:\n  A: Foo |\n',
                'other.raml': LIB + 'types:\n  B: Foo |\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'lib.raml')
        located = {
            (trace.location.rsplit('/', 1)[-1], trace.position.line)
            for chain in caught.value.chains()
            for trace in chain
            if trace.message == 'invalid type expression'
        }
        assert located == {('lib.raml', 5), ('other.raml', 3)}


class TestTypeExprRefs:
    """docs/06 § 3 — one record per name, for tooling."""

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

    def test_a_dot_that_names_no_library_is_part_of_the_name(self, workspace):
        types = library(workspace, '  Dot.Type: string\n  T: Dot.Type\n')
        refs = types['T'].type_expr_refs
        assert [(ref.resolved.name, ref.column, ref.library_link) for ref in refs] == [('Dot.Type', 6, None)]

    def test_an_unqualified_name_emits_one_ref(self, workspace):
        types = library(workspace, '  Thing: string\n  T: Thing\n')
        refs = types['T'].type_expr_refs
        assert len(refs) == 1
        assert refs[0].resolved is types['Thing']
        assert refs[0].library_link is None


class TestTypeExprIsDetached:
    """docs/05 § 1 — after P7 a shape keeps its expression's text and span,
    not the YAML node P7 placed names by."""

    DOCUMENT = (
        '#%RAML 1.0\ntitle: t\n'
        'types:\n'
        '  Base:\n    properties:\n      a: string\n'
        '  Tagged:\n    type: Base\n    facets:\n      level: integer\n    properties:\n      b: string[]\n'
        '  Leveled:\n    type: Tagged\n    level: 2\n'
        '  Either:\n    type: Base | Leveled\n    properties:\n      c: integer?\n'
        '  Both: [Base, Leveled]\n'
        '/r:\n  get:\n    queryParameters:\n      q: Either\n'
        '    responses:\n      200:\n        body:\n          application/json: Tagged[]\n'
    )

    @staticmethod
    def shapes_of(raml):
        """Every shape of this parse anywhere in memory, copies included."""
        gc.collect()
        return [obj for obj in gc.get_objects() if isinstance(obj, BaseShape) and obj._raml is raml]

    @pytest.mark.parametrize('options', [ParseOptions(), ParseOptions(unwrap=True, validate=True)])
    def test_no_shape_keeps_a_node(self, workspace, options):
        raml = workspace.parse(workspace({'api.raml': self.DOCUMENT}) / 'api.raml', options)
        written = [base.type_expr for base in self.shapes_of(raml) if base.type_expr is not None]
        assert written, 'the document writes type expressions'
        assert all(isinstance(expr, WrittenScalar) for expr in written)

    def test_retained_source_keeps_the_node(self, workspace):
        # The tree holds the node anyway; a record beside it only adds memory.
        raml = workspace.parse(workspace({'api.raml': self.DOCUMENT}) / 'api.raml', ParseOptions(retain_source=True))
        written = [base.type_expr for base in raml.shapes if base.type_expr is not None]
        assert written
        assert all(isinstance(expr, Node) for expr in written)

    def test_the_record_is_the_text_and_span_written(self, workspace):
        item = library(workspace, '  T: string[]\n')['T']
        assert (item.type_expr.value, item.type_expr.position.line, item.type_expr.position.column) == (
            'string[]',
            3,
            6,
        )

    def test_an_inner_shape_records_its_template_s_expression(self, workspace):
        array = library(workspace, '  T: string[]\n')['T']
        assert array.shape.items.type_expr == array.type_expr

    @pytest.mark.parametrize(
        'document',
        [
            # P7 fails: the shapes it settled and the one it could not.
            '#%RAML 1.0\ntitle: t\ntypes:\n  A: string[]\n  B: Missing\n',
            # P4 fails, so P7 never runs.
            '#%RAML 1.0\ntitle: t\ntypes:\n  A: string[]\n/r:\n  type: missing\n',
        ],
    )
    def test_a_partial_model_keeps_no_node(self, workspace, document):
        raml, error = workspace.lenient(workspace({'api.raml': document}) / 'api.raml')
        assert error is not None
        written = [base.type_expr for base in self.shapes_of(raml) if base.type_expr is not None]
        assert written
        assert all(isinstance(expr, WrittenScalar) for expr in written)


class TestAnnotationTypes:
    def test_an_annotation_type_resolves_against_annotation_types(self, workspace):
        root = workspace(
            {
                'lib.raml': LIB + 'annotationTypes:\n  Base:\n    properties:\n      a: string\n  Derived: Base\n',
            }
        )
        raml = workspace.parse(root / 'lib.raml')
        declared = raml.annotation_types_in(raml.location)
        assert declared['Derived'].alias is declared['Base']

    def test_an_annotation_type_falls_back_to_types(self, workspace):
        # Spec § Declaring Annotation Types: the syntax is a data type's, and it
        # may extend one (docs/04 § 3).
        root = workspace(
            {'lib.raml': LIB + 'types:\n  Config:\n    properties:\n      a: string\nannotationTypes:\n  Use: Config\n'}
        )
        raml = workspace.parse(root / 'lib.raml')
        assert raml.annotation_types_in(raml.location)['Use'].type == 'object'
