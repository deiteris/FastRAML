"""`DataNode` and the annotated-scalar form.

See docs/03-yaml-and-io.md § 6 and § 7.
"""

from __future__ import annotations

import json
import math

import pytest

from fastraml import ParseOptions, RamlError, path_to_file_uri
from fastraml.datanode import make_data_node, value_node_of
from fastraml.parser.facets import make_string_facet, resolve_annotated_scalar
from fastraml.registry import Raml
from fastraml.types.examples import examples_of
from fastraml.types.shape import make_shape
from fastraml.yamlnode import compose, pairs

#: Several tests here carry their value on an annotation key, which accepts
#: anything. P8 binds every application to a declaration, so they are declared.
API = '#%RAML 1.0\ntitle: T\nannotationTypes:\n  a: any\n  redirectable: any\n  only: any\n'


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def first_value(raml: Raml, text: str):
    """Build a DataNode from the first key of a one-mapping document."""
    root = compose(text, uri='file:///a.raml')
    key, value = next(iter(pairs(root)))
    return make_data_node(raml, key, value, 'file:///a.raml')


class TestScalarConversion:
    @pytest.mark.parametrize(
        ('source', 'expected'),
        [
            ('v: text', 'text'),
            ('v: 42', 42),
            ('v: -7', -7),
            ('v: 0x1f', 31),
            ('v: 0o17', 15),
            # A bare leading zero is octal, as in PyYAML's constructor and in
            # go-yaml's ParseInt base 0: `017` is 15, not 17.
            ('v: 017', 15),
            ('v: -017', -15),
            # ...and a leading zero with no octal reading keeps its text, which
            # is also what go-yaml does with it.
            ('v: 08', '08'),
            ('v: 09', '09'),
            ('v: 1_000', 1000),
            ('v: 1.5', 1.5),
            ('v: true', True),
            ('v: false', False),
            # YAML 1.2: `true` and `false` are the only booleans. `no` is a
            # string, and so are the base-60 forms YAML 1.1 read as numbers.
            ('v: no', 'no'),
            ('v: yes', 'yes'),
            ('v: on', 'on'),
            ('v: 12:30:00', '12:30:00'),
            ('v:', None),
            # A timestamp keeps its literal text: RAML wants the written form
            # of a date-only example, and the type layer parses it.
            ('v: 2015-05-23', '2015-05-23'),
        ],
    )
    def test_a_scalar_takes_its_python_value_from_its_tag(self, source: str, expected: object):
        assert first_value(Raml(), source).raw == expected

    def test_infinity_and_nan_survive(self):
        assert first_value(Raml(), 'v: .inf').raw == math.inf
        assert math.isnan(first_value(Raml(), 'v: .nan').raw)


class TestAnExplicitBoolTagReadsTheCoreSchemaOnly:
    """docs/03 § 2.1: `!!bool` reads the YAML 1.2 core schema on every path.

    A facet reads it through `scalar_bool`, data through `scalar_value`; both
    agree on what is a boolean, and neither reads `yes` as one.
    """

    @staticmethod
    def required_of(text: str) -> bool:
        key, value = next(iter(pairs(compose(text, uri='file:///a.raml'))))
        return make_shape(Raml(), key, value, 'file:///a.raml', 'string').shape.properties['a'].required

    @pytest.mark.parametrize(('text', 'expected'), [('True', True), ('TRUE', True), ('false', False), ('FALSE', False)])
    def test_a_core_spelling_reads_the_same_as_a_facet_and_as_data(self, text, expected):
        assert (
            self.required_of(f'T:\n  type: object\n  properties:\n    a:\n      required: !!bool {text}\n') is expected
        )
        assert first_value(Raml(), f'v: !!bool {text}').raw is expected

    def test_a_non_core_spelling_is_not_a_boolean_facet_value(self):
        with pytest.raises(RamlError) as caught:
            self.required_of('T:\n  type: object\n  properties:\n    a:\n      required: !!bool yes\n')
        assert [trace.message for chain in caught.value.chains() for trace in chain][-1] == 'expected a boolean value'

    @pytest.mark.parametrize('text', ['yes', 'no', 'on', 'y'])
    def test_a_non_core_spelling_keeps_its_text_as_data(self, text):
        assert first_value(Raml(), f'v: !!bool {text}').raw == text

    def test_a_core_spelling_with_a_trailing_newline_is_not_a_boolean(self):
        # A literal block keeps its final newline; `true\n` is not `true`.
        with pytest.raises(RamlError) as caught:
            self.required_of('T:\n  type: object\n  properties:\n    a:\n      required: !!bool |\n        true\n')
        assert [trace.message for chain in caught.value.chains() for trace in chain][-1] == 'expected a boolean value'
        assert first_value(Raml(), 'v: !!bool |\n  true\n').raw == 'true\n'

    def test_a_non_core_example_fails_a_boolean_type(self, workspace):
        root = workspace({'api.raml': API + 'types:\n  T:\n    type: boolean\n    example: !!bool yes\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml', ParseOptions(validate=True))
        [chain] = caught.value.chains()
        assert [(trace.message, trace.info) for trace in chain] == [
            ('invalid example', {}),
            ('invalid type', {'path': '$', 'expected': 'boolean', 'found': 'str'}),
        ]


class TestStructure:
    def test_a_mapping_keeps_per_key_positions_and_a_plain_projection(self):
        node = first_value(Raml(), 'v:\n  a: 1\n  b:\n    - x\n    - y\n')
        assert node.raw == {'a': 1, 'b': ['x', 'y']}

        entries = node.value.mapping.entries
        assert [entry.key for entry in entries] == ['a', 'b']
        assert entries[0].key_pos.line == 2
        assert entries[1].value.sequence.items[1].value_pos.line == 5

    def test_declaration_order_is_preserved(self):
        node = first_value(Raml(), 'v:\n  z: 1\n  a: 2\n  m: 3\n')
        assert [entry.key for entry in node.value.mapping.entries] == ['z', 'a', 'm']
        assert list(node.raw) == ['z', 'a', 'm']

    def test_raw_is_computed_once_at_construction(self):
        node = first_value(Raml(), 'v:\n  a: 1\n')
        assert node.value.raw is node.value.raw

    def test_is_scalar_distinguishes_a_null_from_a_container(self):
        assert first_value(Raml(), 'v:').value.is_scalar is True
        assert first_value(Raml(), 'v: {a: 1}').value.is_scalar is False


class TestInlineJson:
    def test_a_scalar_beginning_with_a_brace_is_parsed_as_json(self):
        # JSON-encoded examples keep their object structure.
        node = first_value(Raml(), 'v: \'{"type": "object", "n": [1, 2]}\'')
        assert node.raw == {'type': 'object', 'n': [1, 2]}
        assert [entry.key for entry in node.value.mapping.entries] == ['type', 'n']

    def test_a_scalar_beginning_with_a_bracket_is_parsed_as_json(self):
        assert first_value(Raml(), "v: '[1, 2, 3]'").raw == [1, 2, 3]

    @pytest.mark.parametrize(
        'text',
        [
            '"the dispossessed" le guin',
            '"unterminated',
            '"',
            '{',
            '[',
            '{"attr": 1]',
            '[1, 2}',
            '{"attr": 1} suffix',
            '[2026-07-20] (John): Hi',
            ' {"attr": 1}',
        ],
    )
    def test_text_without_matching_outer_delimiters_stays_literal(self, text):
        node = first_value(Raml(), f"v: '{text}'")
        assert node.value.is_scalar
        assert node.raw == text

    @pytest.mark.parametrize(
        ('text', 'expected'), [('{"attr": 1}', {'attr': 1}), ('[1, 2]', [1, 2]), ('"text"', 'text')]
    )
    @pytest.mark.parametrize('suffix', ['\n', ' \t\r\n'])
    def test_trailing_json_whitespace_does_not_hide_the_closing_delimiter(self, text, expected, suffix):
        node = first_value(Raml(), 'v: ' + json.dumps(text + suffix))
        assert node.raw == expected

    def test_a_block_scalar_with_a_final_newline_is_decoded(self):
        assert first_value(Raml(), 'v: |\n  "[1, 2]"\n').raw == '[1, 2]'

    @pytest.mark.parametrize(
        ('source', 'expected'),
        [
            (r"""v: '"{\"attr\": 1}"'""", '{"attr": 1}'),
            (r"""v: '"[1, 2]"'""", '[1, 2]'),
            (r"""v: '"[not JSON]"'""", '[not JSON]'),
            (r"""v: '"\"quoted\""'""", '"quoted"'),
            (r"""v: '""'""", ''),
            (r"""v: '"true"'""", 'true'),
        ],
    )
    def test_an_encoded_string_is_decoded_once(self, source, expected):
        node = first_value(Raml(), source)
        assert node.value.is_scalar
        assert node.raw == expected
        assert node.value_pos.line == 1
        assert node.value_pos.column == 4

    def test_collection_children_are_not_decoded_again(self):
        node = first_value(Raml(), r"""v: '{"object": "{\"attr\": 1}", "array": "[1, 2]", "quote": "\"text\""}'""")
        assert node.raw == {'object': '{"attr": 1}', 'array': '[1, 2]', 'quote': '"text"'}

    def test_yaml_collection_children_keep_their_literal_strings(self):
        node = first_value(Raml(), """v:\n  object: '{"attr": 1}'\n  array: '[1, 2]'\n  quote: '"text"'\n""")
        assert node.raw == {'object': '{"attr": 1}', 'array': '[1, 2]', 'quote': '"text"'}

    @pytest.mark.parametrize('text', ['{not json}', '[not JSON]', r'"bad\q"'])
    def test_malformed_json_with_matching_delimiters_is_reported_at_the_value(self, text):
        with pytest.raises(json.JSONDecodeError) as json_error:
            json.loads(text)
        with pytest.raises(RamlError) as caught:
            first_value(Raml(), f"v: '{text}'")
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'invalid inline JSON'
        assert trace.info == {'error': str(json_error.value)}
        assert (trace.location, trace.position.line, trace.position.column) == ('file:///a.raml', 1, 4)


class TestEncodedStringSites:
    @pytest.mark.parametrize('unwrap', [False, True])
    @pytest.mark.parametrize('validate', [False, True])
    @pytest.mark.parametrize('value', ['{"installation_id":0}', '[2026-07-20] (John): Hi', '"quoted"'])
    def test_the_same_encoding_works_at_every_data_value_root(self, workspace, unwrap, validate, value):
        encoded = "'" + json.dumps(value) + "'"
        source = f"""#%RAML 1.0
title: T
annotationTypes:
  literal: string
(literal): {encoded}
types:
  Single:
    type: string
    enum: [{encoded}]
    default: {encoded}
    example: {encoded}
  Wrapped:
    type: string
    example:
      value: {encoded}
  Named:
    type: string
    examples:
      literal: {encoded}
  Included:
    type: string
    example: !include example.yaml
  Fragment:
    type: string
    examples: !include examples.raml
  Base:
    type: string
    facets:
      literal: string
  Child:
    type: Base
    literal: {encoded}
/x:
  get:
    queryParameters:
      since:
        type: string
        example: {encoded}
"""
        root = workspace(
            {
                'api.raml': source,
                'example.yaml': f'value: {encoded}\n',
                'examples.raml': f'#%RAML 1.0 NamedExample\nliteral:\n  value: {encoded}\n',
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=unwrap, validate=validate))
        types = raml.types_in(raml.location)
        for name in ('Single', 'Wrapped', 'Named', 'Included', 'Fragment'):
            assert [example.data.raw for example in examples_of(types[name])] == [value], name
        assert [member.raw for member in types['Single'].enum] == [value]
        assert types['Single'].default.raw == value
        assert raml.entry_point.annotations['literal'].value.raw == value
        assert types['Child'].custom_facets['literal'].raw == value
        parameter = raml.endpoints['/x'].operations['get'].request.query_parameters['since']
        assert parameter.base.example.data.raw == value


class TestIncludedValues:
    def test_an_included_value_reports_the_included_file_as_its_location(self, workspace):
        # A bad example inside an included file must point at that file.
        root = workspace({'api.raml': API + '(a): !include data.yaml\n', 'data.yaml': 'k: v\n'})
        raml = workspace.parse(root / 'api.raml')
        value = raml.entry_point.annotations['a'].value
        assert value.location == path_to_file_uri(root / 'data.yaml')
        assert value.include.path == 'data.yaml'
        assert value.value_pos.line == API.count('\n') + 1, 'the value position stays at the !include directive'


class TestValueNodeOf:
    def test_a_plain_python_value_can_be_wrapped(self):
        node = value_node_of({'a': [1, {'b': 2}]})
        assert node.raw == {'a': [1, {'b': 2}]}
        assert node.mapping.entries[0].value.sequence.items[1].value.mapping.entries[0].key == 'b'


class TestAnnotatedScalar:
    def test_a_plain_scalar_passes_through(self):
        root = compose('v: text\n', uri='file:///a.raml')
        _key, value = next(iter(pairs(root)))
        node, extensions = resolve_annotated_scalar(Raml(), value, 'file:///a.raml')
        assert (node.value, extensions) == ('text', {})

    def test_the_map_form_yields_the_value_and_its_annotations(self, workspace):
        root = workspace({'api.raml': API + 'description:\n  value: Some text\n  (redirectable): true\n'})
        api = workspace.parse(root / 'api.raml').entry_point
        assert api.description.value == 'Some text'
        assert api.description.annotations['redirectable'].value.raw is True

    def test_a_missing_value_key_is_an_error(self, workspace):
        root = workspace({'api.raml': API + 'description:\n  (only): 1\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml')
        assert 'missing value key in annotated scalar' in caught.value.messages()[0]

    def test_any_other_key_is_an_error(self, workspace):
        root = workspace({'api.raml': API + 'description:\n  value: text\n  other: 1\n'})
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'api.raml')
        trace = next(iter(caught.value.chains()))[-1]
        assert trace.message == 'unknown field in annotated scalar'
        assert trace.info == {'field': 'other'}

    def test_the_form_works_at_every_scalar_facet_because_one_builder_serves_all(self, workspace):
        # title, description, version and baseUri all go through
        # make_scalar_facet, so supporting one supports all of them.
        root = workspace(
            {
                'api.raml': (
                    '#%RAML 1.0\n'
                    'annotationTypes:\n  a: any\n  b: any\n  c: any\n'
                    'title:\n  value: T\n  (a): 1\n'
                    'version:\n  value: v1\n  (b): 2\n'
                    'baseUri:\n  value: http://e.com\n  (c): 3\n'
                )
            }
        )
        api = workspace.parse(root / 'api.raml').entry_point
        assert (api.title.value, api.version.value, api.base_uri.value) == ('T', 'v1', 'http://e.com')
        assert [set(facet.annotations) for facet in (api.title, api.version, api.base_uri)] == [
            {'a'},
            {'b'},
            {'c'},
        ]


class TestFacetIncludes:
    def test_a_facet_may_be_included_from_a_non_yaml_file(self, workspace):
        root = workspace({'api.raml': API + 'description: !include d.md\n', 'd.md': 'Text from markdown.\n'})
        api = workspace.parse(root / 'api.raml').entry_point
        assert api.description.value == 'Text from markdown.\n'
        assert api.description.include.abs_uri == path_to_file_uri(root / 'd.md')

    def test_a_non_scalar_at_a_scalar_facet_is_rejected(self):
        raml = Raml()
        root = compose('description:\n  - a\n', uri='file:///a.raml')
        key, value = next(iter(pairs(root)))
        with pytest.raises(RamlError) as caught:
            make_string_facet(raml, key, value, 'file:///a.raml')
        assert 'expected scalar or mapping node' in caught.value.messages()[0]
