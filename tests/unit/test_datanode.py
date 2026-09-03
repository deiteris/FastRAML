"""`DataNode` and the annotated-scalar form.

See docs/03-yaml-and-io.md sections 6 and 7.
"""

from __future__ import annotations

import math

import pytest

from pyraml import RamlError, parse_from_path, path_to_file_uri
from pyraml.datanode import make_data_node, value_node_of
from pyraml.parser.facets import make_string_facet, resolve_annotated_scalar
from pyraml.registry import Raml
from pyraml.yamlnode import compose, pairs

API = '#%RAML 1.0\ntitle: T\n'


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
            ('v: 1_000', 1000),
            ('v: 1.5', 1.5),
            ('v: true', True),
            ('v: no', False),
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
        # This is how `type: '{"type":"object"}'` and inline JSON examples work.
        node = first_value(Raml(), 'v: \'{"type": "object", "n": [1, 2]}\'')
        assert node.raw == {'type': 'object', 'n': [1, 2]}
        assert [entry.key for entry in node.value.mapping.entries] == ['type', 'n']

    def test_a_scalar_beginning_with_a_bracket_is_parsed_as_json(self):
        assert first_value(Raml(), "v: '[1, 2, 3]'").raw == [1, 2, 3]

    def test_malformed_inline_json_is_reported_at_the_value(self):
        with pytest.raises(RamlError) as caught:
            first_value(Raml(), "v: '{not json}'")
        assert next(iter(caught.value.chains()))[-1].message == 'invalid inline JSON'


class TestIncludedValues:
    def test_an_included_value_reports_the_included_file_as_its_location(self, workspace):
        # A bad example inside an included file must point at that file.
        root = workspace({'api.raml': API + '(a): !include data.yaml\n', 'data.yaml': 'k: v\n'})
        raml = parse_from_path(root / 'api.raml')
        value = raml.entry_point.annotations['a'].value
        assert value.location == path_to_file_uri(root / 'data.yaml')
        assert value.include.path == 'data.yaml'
        assert value.value_pos.line == 3, 'the value position stays at the !include directive'


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
        api = parse_from_path(root / 'api.raml').entry_point
        assert api.description.value == 'Some text'
        assert api.description.annotations['redirectable'].value.raw is True

    def test_a_missing_value_key_is_an_error(self, workspace):
        root = workspace({'api.raml': API + 'description:\n  (only): 1\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
        assert 'missing value key in annotated scalar' in caught.value.messages()[0]

    def test_any_other_key_is_an_error(self, workspace):
        root = workspace({'api.raml': API + 'description:\n  value: text\n  other: 1\n'})
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml')
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
                    'title:\n  value: T\n  (a): 1\n'
                    'version:\n  value: v1\n  (b): 2\n'
                    'baseUri:\n  value: http://e.com\n  (c): 3\n'
                )
            }
        )
        api = parse_from_path(root / 'api.raml').entry_point
        assert (api.title.value, api.version.value, api.base_uri.value) == ('T', 'v1', 'http://e.com')
        assert [set(facet.annotations) for facet in (api.title, api.version, api.base_uri)] == [
            {'a'},
            {'b'},
            {'c'},
        ]


class TestFacetIncludes:
    def test_a_facet_may_be_included_from_a_non_yaml_file(self, workspace):
        root = workspace({'api.raml': API + 'description: !include d.md\n', 'd.md': 'Text from markdown.\n'})
        api = parse_from_path(root / 'api.raml').entry_point
        assert api.description.value == 'Text from markdown.\n'
        assert api.description.include.abs_uri == path_to_file_uri(root / 'd.md')

    def test_a_non_scalar_at_a_scalar_facet_is_rejected(self):
        raml = Raml()
        root = compose('description:\n  - a\n', uri='file:///a.raml')
        key, value = next(iter(pairs(root)))
        with pytest.raises(RamlError) as caught:
            make_string_facet(raml, key, value, 'file:///a.raml')
        assert 'expected scalar or mapping node' in caught.value.messages()[0]
