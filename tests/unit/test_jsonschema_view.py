"""`views/jsonschema.py` — a shape as JSON Schema draft-07.

Two halves. The structural tests pin decisions go-raml's own converter
makes and a reader would not guess. The differential half is the one that
matters: for each value, the RAML shape and the emitted schema must return the
same verdict, checked by `jsonschema` rather than by this project's own reader.
"""

from __future__ import annotations

import re

import jsonschema
import pytest

from fastraml import ParseOptions
from fastraml.types.values import (
    DATETIME_ONLY_PATTERN,
    RFC2616_PATTERN,
    TIME_ONLY_PATTERN,
    parse_rfc2616,
    valid_datetime_only,
    valid_time_only,
)
from fastraml.views.jsonschema import SCHEMA_VERSION, to_json_schema

API = '#%RAML 1.0\ntitle: T\n'


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def converted(workspace, body: str, name: str = 'T'):
    """The named type's schema and what the conversion dropped."""
    root = workspace({'api.raml': API + 'types:\n' + body})
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    return to_json_schema(raml.types_in(raml.location)[name])


def both(workspace, body: str, name: str = 'T'):
    """The RAML shape and its schema, for comparing verdicts."""
    root = workspace({'api.raml': API + 'types:\n' + body})
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
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
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
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
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
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
            ('time-only', {'type': 'string', 'pattern': TIME_ONLY_PATTERN}),
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
        assert node['pattern'] == RFC2616_PATTERN


class TestUnwrapped:
    def test_a_declared_shape_is_refused(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  P:\n    properties:\n      a: string\n  T:\n    type: P\n'})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=False))
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


#: One corpus per exported date/time pattern: values the parser accepts and
#: values it rejects, at each boundary the pattern spells out.
_DATE_TIME_CORPUS = {
    'time-only': (
        valid_time_only,
        TIME_ONLY_PATTERN,
        ['00:00:00', '23:59:59', '12:30:00.5', '12:30:00.123456', '23:59:60'],
        [
            '24:00:00',
            '12:60:00',
            '12:00:61',
            '12:00',
            '12:00:00.',
            '12:00:00Z',
            '12:00:00+01:00',
            '1:00:00',
            '12:00:00\n',
        ],
    ),
    'datetime-only': (
        valid_datetime_only,
        DATETIME_ONLY_PATTERN,
        [
            '2024-01-31T00:00:00',
            '2024-02-29T12:00:00',
            '2000-02-29T12:00:00',
            '0000-02-29T00:00:00',
            '2023-04-30T23:59:60',
            '2023-12-01T08:00:00.25',
        ],
        [
            '2023-00-10T00:00:00',
            '2023-01-00T00:00:00',
            '2023-13-01T00:00:00',
            '2023-04-31T00:00:00',
            '2023-02-29T00:00:00',
            '1900-02-29T00:00:00',
            '2023-01-01T24:00:00',
            '2023-01-01 00:00:00',
            '2023-01-01T00:00:00Z',
            '2023-01-01T00:00:00\n',
        ],
    ),
    'rfc2616': (
        parse_rfc2616,
        RFC2616_PATTERN,
        [
            'Sun, 06 Nov 1994 08:49:37 GMT',
            'Thu, 29 Feb 2024 00:00:00 GMT',
            'Sat, 30 Apr 2022 23:59:60 GMT',
            'Mon, 31 Dec 1999 23:59:59 GMT',
        ],
        [
            'Sun, 00 Nov 1994 08:49:37 GMT',
            'Sun, 31 Nov 1994 08:49:37 GMT',
            'Tue, 29 Feb 2023 00:00:00 GMT',
            'Sun, 06 Nov 1994 24:00:00 GMT',
            'Sun, 06 Nov 1994 08:49:37.5 GMT',
            'Sunday, 06-Nov-94 08:49:37 GMT',
            'Sun, 6 Nov 1994 08:49:37 GMT',
            'Sun, 06 Nov 1994 08:49:37 GMT\n',
        ],
    ),
}


class TestDateTimePatternsMatchTheParser:
    r"""An exported date/time pattern accepts exactly what the parser accepts.

    The patterns use only syntax ECMA-262 and Python `re` read alike (ASCII
    classes, non-capturing groups, `^` and `(?![\s\S])` with no flags), so
    Python's `re.search` -- what a JSON Schema validator applies, in Python or
    in ECMA-262 -- gives the verdict either language gives. A trailing newline
    is in the corpus because Python's `$` would have let it through.
    """

    @pytest.mark.parametrize('kind', list(_DATE_TIME_CORPUS))
    def test_verdicts_match(self, kind):
        validator, pattern, valid, invalid = _DATE_TIME_CORPUS[kind]
        compiled = re.compile(pattern)
        for value in valid + invalid:
            by_parser = validator(value)
            assert by_parser == (value in valid), value
            assert (compiled.search(value) is not None) == by_parser, value

    @pytest.mark.parametrize('kind', list(_DATE_TIME_CORPUS))
    def test_the_pattern_reaches_a_schema_validator(self, workspace, kind):
        _, _, valid, invalid = _DATE_TIME_CORPUS[kind]
        body = '  T:\n    type: datetime\n    format: rfc2616\n' if kind == 'rfc2616' else f'  T: {kind}\n'
        shape, schema = both(workspace, body)
        for value in valid + invalid:
            assert accepts(schema, value) == (shape.validate(value) is None) == (value in valid), value


def test_the_output_is_a_valid_draft_07_schema(workspace):
    schema, _ = converted(
        workspace,
        '  T:\n    properties:\n      a: string\n      b?: T[]\n      c?: string | integer\n',
    )
    assert schema['$schema'] == SCHEMA_VERSION
    # Raises if the document is not a well-formed schema.
    jsonschema.Draft7Validator.check_schema(schema)


@pytest.mark.parametrize(
    ('properties', 'values'),
    [
        ('name: string\n      /^name$/: integer', [({'name': 'abc'}, True), ({'name': 1}, False)]),
        (
            "'a.b?': {type: string, required: true}\n      //: integer",
            [({'a.b?': 'abc'}, True), ({'a.b?': 1}, False), ({'axb': 1, 'a.b?': 'abc'}, True)],
        ),
        (
            'name: string\n      /x/: integer',
            [({'name': 'a', 'prefix': 1}, True), ({'name': 'a', 'prefix': 's'}, False), ({'name': 'a', 'z': 1}, False)],
        ),
        (
            '/x/: string\n      /xy/: integer',
            [({'xy': 'a'}, True), ({'xy': 1}, False), ({'beforexyafter': 'a'}, True), ({'z': 1}, False)],
        ),
        (
            '/xy/: integer\n      /x/: string',
            [({'xy': 1}, True), ({'xy': 'a'}, False), ({'x': 'a'}, True), ({'z': 1}, False)],
        ),
        (
            '"line\\nbreak": string\n      //: integer',
            [({'line\nbreak': 'a'}, True), ({'line\nbreak': 1}, False), ({'other\nkey': 1, 'line\nbreak': 'a'}, True)],
        ),
    ],
    ids=['explicit-wins', 'literal-metacharacters', 'search-and-exhaustive', 'first-pattern', 'reversed', 'newlines'],
)
@pytest.mark.parametrize('additional', ['', '    additionalProperties: true\n'])
def test_pattern_precedence_agrees_with_raml(workspace, properties, values, additional):
    """docs/05 § 4: explicit names, then first pattern; unmatched extras fail."""
    shape, schema = both(workspace, f'  T:\n{additional}    properties:\n      {properties}\n')
    jsonschema.Draft7Validator.check_schema(schema)
    for value, expected in values:
        assert (shape.validate(value) is None) is expected
        assert accepts(schema, value) is expected


@pytest.mark.parametrize(
    ('properties', 'value'),
    [
        (r'/(x)/: string' + '\n      ' + r'/(a)\1/: integer', {'aa': 1}),
        (r'/(?P<part>x)/: string' + '\n      ' + r'/(?P<part>a)(?P=part)/: integer', {'aa': 1}),
        ('/(?i)x/: string\n      /y/: integer', {'X': 'a'}),
    ],
    ids=['numbered-backreference', 'named-backreference', 'global-flags'],
)
def test_pattern_export_keeps_capture_scopes_and_reports_unsupported_precedence(workspace, properties, value):
    root = workspace({'api.raml': API + f'types:\n  T:\n    properties:\n      {properties}\n'})
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    shape = raml.types_in(raml.location)['T']
    schema, dropped = to_json_schema(shape)
    jsonschema.Draft7Validator.check_schema(schema)
    definition = schema['definitions']['T']
    # Composing them would renumber or redefine a capture group.
    assert list(definition['patternProperties']) == list(shape.shape.pattern_properties)
    assert shape.validate(value) is None
    # Not `accepts`: python-jsonschema checks `additionalProperties` against one
    # alternation of every pattern, which itself shifts capture groups. Check
    # the per-pattern verdict and the name restriction separately.
    assert accepts({key: kept for key, kept in definition.items() if key != 'additionalProperties'}, value)
    assert all(any(re.search(pattern, key) for pattern in definition['patternProperties']) for key in value)
    assert definition['additionalProperties'] is False
    assert any('precedence' in notice for notice in dropped)


def test_explicit_names_win_when_capture_groups_prevent_pattern_ordering(workspace):
    """Excluding names adds no group, so only the order between patterns is lost."""
    shape, schema = both(
        workspace, '  T:\n    properties:\n      name: string\n      /(n)ame/: integer\n      /z/: string\n'
    )
    _, dropped = to_json_schema(shape)
    assert [notice.partition(': ')[2] for notice in dropped] == [
        'pattern order precedence with capture groups or global inline flags'
    ]
    for value, expected in [({'name': 'a'}, True), ({'name': 'a', 'rename': 1}, True), ({'name': 1}, False)]:
        assert (shape.validate(value) is None) is expected
        assert accepts(schema, value) is expected


def test_explicit_names_escape_only_ecma_syntax_characters(workspace):
    """Unicode-mode ECMA-262, ajv's default, rejects identity escapes like `\\-`."""
    shape, schema = both(
        workspace,
        "  T:\n    properties:\n      'first name': string\n      content-type: string\n"
        "      'a#b&c~d': string\n      //: integer\n",
    )
    (pattern,) = schema['definitions']['T']['patternProperties']
    assert '(?:first name|content-type|a#b&c~d)' in pattern
    names = {'first name': 'a', 'content-type': 'b', 'a#b&c~d': 'c'}
    for value, expected in [(names, True), ({**names, 'x': 1}, True), ({**names, 'x': 'a'}, False)]:
        assert (shape.validate(value) is None) is expected
        assert accepts(schema, value) is expected


@pytest.mark.parametrize(
    ('properties', 'values'),
    [
        (
            'aa: string\n      ' + r'/(a)\1/: integer',
            [({'aa': 'x', 'aaaa': 1}, True), ({'aa': 'x', 'aaaa': 'x'}, False)],
        ),
        ('/x/: string\n      ' + r'/(a)\1/: integer', [({'aa': 1}, True), ({'aa': 'x'}, False), ({'aax': 'x'}, True)]),
    ],
    ids=['single-capture-scope', 'capture-in-last-pattern'],
)
def test_pattern_export_preserves_precedence_when_no_capture_scope_is_shifted(workspace, properties, values):
    root = workspace({'api.raml': API + f'types:\n  T:\n    properties:\n      {properties}\n'})
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    shape = raml.types_in(raml.location)['T']
    schema, dropped = to_json_schema(shape)
    assert dropped == []
    for value, expected in values:
        assert (shape.validate(value) is None) is expected
        assert accepts(schema, value) is expected
