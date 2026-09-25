"""`views/jsonschema.py` — a shape as JSON Schema draft-07.

Two halves. The structural tests pin decisions go-raml's own converter
makes and a reader would not guess. The differential half is the one that
matters: for each value, the RAML shape and the emitted schema must return the
same verdict, checked by `jsonschema` rather than by this project's own reader.
"""

from __future__ import annotations

import jsonschema
import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views.jsonschema import SCHEMA_VERSION, to_json_schema

API = '#%RAML 1.0\ntitle: T\n'


def converted(workspace, body: str, name: str = 'T'):
    """The named type's schema and what the conversion dropped."""
    root = workspace({'api.raml': API + 'types:\n' + body})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    return to_json_schema(raml.types_in(raml.location)[name])


def both(workspace, body: str, name: str = 'T'):
    """The RAML shape and its schema, for comparing verdicts."""
    root = workspace({'api.raml': API + 'types:\n' + body})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    shape = raml.types_in(raml.location)[name]
    schema, _ = to_json_schema(shape)
    return shape, schema


def accepts(schema, value) -> bool:
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError:
        return False
    return True


class TestOnlyTheEntryPointIsADefinition:
    """A property's shape carries the *property's* name, not a type name."""

    def test_a_property_is_written_where_it_stands(self, workspace):
        schema, _ = converted(workspace, '  T:\n    properties:\n      name: string\n')
        assert schema['$ref'] == '#/definitions/T'
        assert list(schema['definitions']) == ['T']
        assert schema['definitions']['T']['properties']['name'] == {'type': 'string'}

    def test_an_anonymous_entry_point_is_named_root(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  T:\n    properties:\n      a: string\n'})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        prop = raml.types_in(raml.location)['T'].shape.properties['a'].base
        schema, _ = to_json_schema(prop)
        assert schema['$ref'] == '#/definitions/a'


class TestRecursion:
    def test_a_cycle_closes_with_a_ref(self, workspace):
        schema, _ = converted(workspace, '  T:\n    properties:\n      name: string\n      next?: T\n')
        assert list(schema['definitions']) == ['T']
        assert schema['definitions']['T']['properties']['next'] == {'$ref': '#/definitions/T'}

    def test_a_cycle_through_an_array_closes_too(self, workspace):
        schema, _ = converted(workspace, '  T:\n    properties:\n      name: string\n      kids?: T[]\n')
        kids = schema['definitions']['T']['properties']['kids']
        assert kids['items'] == {'$ref': '#/definitions/T'}


class TestFacets:
    @pytest.mark.parametrize(
        ('body', 'expected'),
        [
            ('  T:\n    type: string\n    minLength: 2\n    pattern: ^x\n', {'minLength': 2, 'pattern': '^x'}),
            ('  T:\n    type: array\n    items: string\n    uniqueItems: true\n', {'uniqueItems': True}),
            ('  T:\n    type: object\n    minProperties: 1\n', {'minProperties': 1}),
            ('  T:\n    type: integer\n    minimum: 1\n    maximum: 9\n', {'minimum': 1, 'maximum': 9}),
        ],
    )
    def test_a_facet_reaches_the_schema(self, workspace, body, expected):
        schema, _ = converted(workspace, body)
        node = schema['definitions']['T']
        assert expected.items() <= node.items()

    def test_an_exact_multiple_of_is_not_rounded(self, workspace):
        # The facet is a `Fraction` built from the raw text, so this is 1.1 and
        # not the binary approximation of it (docs/10 § 5).
        schema, _ = converted(workspace, '  T:\n    type: number\n    multipleOf: 1.1\n')
        assert schema['definitions']['T']['multipleOf'] == 1.1

    def test_an_integral_bound_stays_an_integer(self, workspace):
        schema, _ = converted(workspace, '  T:\n    type: number\n    minimum: 2\n')
        assert schema['definitions']['T']['minimum'] == 2
        assert isinstance(schema['definitions']['T']['minimum'], int)

    def test_a_pattern_property_key_is_the_bare_regex(self, workspace):
        # RAML writes `/^x-/`; `patternProperties` keys carry no delimiters, and
        # P2 has already stripped them by the time this runs.
        schema, _ = converted(workspace, '  T:\n    properties:\n      /^x-/: string\n')
        assert list(schema['definitions']['T']['patternProperties']) == ['^x-']

    def test_the_common_facets_are_carried(self, workspace):
        body = (
            '  T:\n    type: string\n    displayName: A name\n    description: what it is\n'
            '    default: x\n    example: y\n'
        )
        node = converted(workspace, body)[0]['definitions']['T']
        assert node['title'] == 'A name'
        assert node['description'] == 'what it is'
        assert node['default'] == 'x'
        assert node['examples'] == ['y']

    def test_an_example_without_data_is_left_out(self, workspace):
        # A model assembled in Python may carry one; it has nothing to write.
        root = workspace({'api.raml': API + 'types:\n  T:\n    type: string\n    example: y\n'})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        shape = raml.types_in(raml.location)['T']
        shape.example.data = None
        assert 'examples' not in to_json_schema(shape)[0]['definitions']['T']


class TestKinds:
    @pytest.mark.parametrize(
        ('declared', 'expected'),
        [
            ('string', {'type': 'string'}),
            ('integer', {'type': 'integer'}),
            ('number', {'type': 'number'}),
            ('boolean', {'type': 'boolean'}),
            ('nil', {'type': 'null'}),
            ('datetime', {'type': 'string', 'format': 'date-time'}),
            ('date-only', {'type': 'string', 'format': 'date'}),
            ('time-only', {'type': 'string', 'format': 'time'}),
            ('any', {}),
        ],
    )
    def test_each_kind(self, workspace, declared, expected):
        schema, _ = converted(workspace, f'  T: {declared}\n')
        assert schema['definitions']['T'] == expected

    def test_a_union_is_any_of(self, workspace):
        schema, _ = converted(workspace, '  T: string | integer\n')
        assert schema['definitions']['T']['anyOf'] == [{'type': 'string'}, {'type': 'integer'}]

    def test_a_file_carries_its_encoding(self, workspace):
        schema, dropped = converted(workspace, '  T:\n    type: file\n    fileTypes: [image/png, image/jpeg]\n')
        node = schema['definitions']['T']
        assert node['type'] == 'string'
        assert node['contentEncoding'] == 'base64'
        assert node['contentMediaType'] == 'image/png'
        # JSON Schema carries one media type where RAML allows a list.
        assert any('fileTypes' in message for message in dropped)

    def test_rfc2616_becomes_a_pattern(self, workspace):
        # JSON Schema has no format for it, so go-raml writes the grammar out.
        schema, _ = converted(workspace, '  T:\n    type: datetime\n    format: rfc2616\n')
        node = schema['definitions']['T']
        assert 'format' not in node
        assert node['pattern'].startswith('^(Mon|Tue')


class TestUnwrapped:
    def test_a_declared_shape_is_refused(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  P:\n    properties:\n      a: string\n  T:\n    type: P\n'})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=False))
        with pytest.raises(AssertionError, match='unwrapped shape'):
            to_json_schema(raml.types_in(raml.location)['T'])

    def test_inherited_facets_are_present_after_unwrap(self, workspace):
        body = '  P:\n    properties:\n      a: string\n  T:\n    type: P\n    properties:\n      b: integer\n'
        schema, _ = converted(workspace, body)
        assert sorted(schema['definitions']['T']['properties']) == ['a', 'b']


class TestTheSchemaAgreesWithTheShape:
    """The gate: `jsonschema` and fastRAML must reach the same verdict."""

    @pytest.mark.parametrize(
        ('body', 'values'),
        [
            ('  T:\n    type: string\n    minLength: 2\n', ['ab', 'a', 'abc']),
            ('  T:\n    type: integer\n    minimum: 1\n    maximum: 9\n', [1, 0, 9, 10, 5]),
            ('  T:\n    type: array\n    items: string\n    minItems: 1\n', [['a'], [], ['a', 'b']]),
            ('  T: string | integer\n', ['x', 1, True, None]),
            ('  T:\n    type: string\n    enum: [red, green]\n', ['red', 'blue']),
            (
                '  T:\n    properties:\n      a: string\n      b?: integer\n',
                [{'a': 'x'}, {'a': 'x', 'b': 1}, {'b': 1}, {'a': 1}],
            ),
            (
                '  T:\n    properties:\n      a: string\n    additionalProperties: false\n',
                [{'a': 'x'}, {'a': 'x', 'z': 1}],
            ),
            ('  T:\n    properties:\n      /^x-/: string\n', [{'x-a': 'v'}, {'x-a': 1}]),
            ('  T:\n    properties:\n      name: string\n      next?: T\n', [{'name': 'a'}, {'name': 'a', 'next': {}}]),
        ],
        ids=['string', 'integer', 'array', 'union', 'enum', 'object', 'closed', 'pattern', 'recursive'],
    )
    def test_verdicts_match(self, workspace, body, values):
        shape, schema = both(workspace, body)
        for value in values:
            by_raml = shape.validate(value) is None
            by_schema = accepts(schema, value)
            assert by_raml == by_schema, f'{value!r}: raml={by_raml} jsonschema={by_schema}'

    def test_the_cases_are_not_all_one_verdict(self, workspace):
        _, schema = both(workspace, '  T:\n    type: string\n    minLength: 2\n')
        assert {accepts(schema, value) for value in ('ab', 'a')} == {True, False}


def test_the_output_is_a_valid_draft_07_schema(workspace):
    schema, _ = converted(
        workspace,
        '  T:\n    properties:\n      a: string\n      b?: T[]\n      c?: string | integer\n',
    )
    assert schema['$schema'] == SCHEMA_VERSION
    # Raises if the document is not a well-formed schema.
    jsonschema.Draft7Validator.check_schema(schema)
