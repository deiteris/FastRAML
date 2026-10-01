"""JSON Schema -> RAML document export, including parse-back validity."""

from __future__ import annotations

import json

import pytest
import yaml

from fastraml import ParseOptions, RamlError, to_raml
from fastraml.parser.fragments import DataTypeFragment, Library


def export(workspace, schema: dict, **files: str) -> str:
    root = workspace(
        {
            'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  Root: !include schema.json\n',
            'schema.json': json.dumps(schema),
            **files,
        }
    )
    parsed = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    return to_raml(parsed.types_in(parsed.location)['Root'].shape)


def reparse(workspace, text: str):
    root = workspace({'export.raml': text})
    return workspace.parse(root / 'export.raml', ParseOptions(unwrap=True, validate=True)).entry_point


def test_a_simple_schema_exports_a_data_type_with_exact_facets(memory_workspace):
    text = export(
        memory_workspace,
        {
            'type': 'object',
            'properties': {'amount': {'type': 'number', 'multipleOf': 1.1}, 'note': {'type': 'string'}},
            'required': ['amount'],
        },
    )
    assert text.startswith('#%RAML 1.0 DataType\n')
    body = yaml.safe_load(text.partition('\n')[2])
    assert body['properties']['amount']['multipleOf'] == 1.1
    assert body['properties']['note']['required'] is False
    parsed = reparse(memory_workspace, text)
    assert isinstance(parsed, DataTypeFragment)
    assert parsed.shape.validate({'amount': 2.2}) is None
    assert parsed.shape.validate({'amount': 2.3}) is not None


def test_unused_definitions_still_export_a_library(memory_workspace):
    text = export(
        memory_workspace,
        {
            'definitions': {'Unused': {'type': 'string', 'minLength': 3}},
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
        },
    )
    assert text.startswith('#%RAML 1.0 Library\n')
    assert list(yaml.safe_load(text.partition('\n')[2])['types']) == ['Unused', 'schema']
    parsed = reparse(memory_workspace, text)
    assert isinstance(parsed, Library)
    assert parsed.types['Unused'].validate('abc') is None
    assert parsed.types['Unused'].validate('a') is not None


def test_all_of_with_a_definitions_only_reference_exports_the_concrete_type(memory_workspace):
    text = export(
        memory_workspace,
        {
            'allOf': [
                {'$ref': 'base.json'},
                {'type': 'object', 'properties': {'code': {'type': 'string', 'enum': ['X']}}},
            ]
        },
        **{'base.json': json.dumps({'definitions': {'error': {'type': 'object'}}})},
    )
    parsed = reparse(memory_workspace, text)
    assert isinstance(parsed, DataTypeFragment)
    assert parsed.shape.type == 'object'
    assert list(parsed.shape.shape.properties) == ['code']
    assert parsed.shape.validate({'code': 'X'}) is None
    assert parsed.shape.validate({'code': 'Y'}) is not None
    assert parsed.shape.validate('X') is not None


@pytest.mark.parametrize('conjunction', [False, True])
def test_uuid_format_exports_as_a_bounded_pattern_and_round_trips(memory_workspace, conjunction):
    schema = {'type': 'string', 'format': 'uuid'}
    if conjunction:
        schema = {'allOf': [schema, {'minLength': 20, 'maxLength': 40}]}
    text = export(memory_workspace, schema)
    body = yaml.safe_load(text.partition('\n')[2])
    assert body['type'] == 'string'
    assert body['minLength'] == body['maxLength'] == 36
    assert 'pattern' in body
    parsed = reparse(memory_workspace, text)
    assert isinstance(parsed, DataTypeFragment)
    value = '123e4567-e89b-12d3-a456-426614174000'
    assert parsed.shape.validate(value) is None
    assert parsed.shape.validate(value.upper()) is None
    assert parsed.shape.validate('bad') is not None
    assert parsed.shape.validate(value + '\n') is not None


def test_defs_are_exported_even_without_references(memory_workspace):
    text = export(
        memory_workspace,
        {
            '$defs': {'Code': {'type': 'integer', 'minimum': 1}},
            'type': 'boolean',
        },
    )
    types = yaml.safe_load(text.partition('\n')[2])['types']
    assert list(types) == ['Code', 'schema']
    parsed = reparse(memory_workspace, text)
    assert parsed.types['Code'].validate(1) is None
    assert parsed.types['Code'].validate(0) is not None


def test_same_named_definitions_from_both_keywords_are_retained_in_source_order(memory_workspace):
    text = export(
        memory_workspace,
        {
            '$defs': {'A': {'type': 'integer'}},
            'definitions': {'A': {'type': 'string'}},
            'type': 'object',
        },
    )
    assert list(yaml.safe_load(text.partition('\n')[2])['types']) == ['A', 'A2', 'schema']
    parsed = reparse(memory_workspace, text)
    assert parsed.types['A'].validate(1) is None
    assert parsed.types['A2'].validate('ok') is None


def test_same_named_definitions_from_both_keywords_keep_distinct_references(memory_workspace):
    text = export(
        memory_workspace,
        {
            'definitions': {'A': {'type': 'string'}},
            '$defs': {'A': {'type': 'integer'}},
            'type': 'object',
            'properties': {
                'old': {'$ref': '#/definitions/A'},
                'new': {'$ref': '#/$defs/A'},
            },
        },
    )
    types = yaml.safe_load(text.partition('\n')[2])['types']
    assert types['schema']['properties']['old']['type'] == 'A'
    assert types['schema']['properties']['new']['type'] == 'A2'
    parsed = reparse(memory_workspace, text)
    assert parsed.types['schema'].validate({'old': 'x', 'new': 2}) is None
    assert parsed.types['schema'].validate({'old': 2, 'new': 'x'}) is not None


def test_a_root_external_ref_without_definitions_can_be_inlined(memory_workspace):
    text = export(memory_workspace, {'$ref': 'uuid.json'}, **{'uuid.json': json.dumps({'type': 'string'})})
    assert text.startswith('#%RAML 1.0 DataType\n')
    assert yaml.safe_load(text.partition('\n')[2]) == {'type': 'string'}
    assert reparse(memory_workspace, text).shape.validate('x') is None


def test_a_recursive_root_uses_a_named_library_type(memory_workspace):
    text = export(memory_workspace, {'type': 'object', 'properties': {'next': {'$ref': '#'}}})
    types = yaml.safe_load(text.partition('\n')[2])['types']
    assert text.startswith('#%RAML 1.0 Library\n')
    assert types['schema']['properties']['next'] == {'type': 'schema', 'required': False}
    assert reparse(memory_workspace, text).types['schema'].validate({'next': {'next': {}}}) is None


def test_an_inline_recursive_root_requires_a_name(memory_workspace):
    raw = json.dumps({'type': 'object', 'properties': {'next': {'$ref': '#'}}})
    root = memory_workspace({'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  Inline:\n    type: |\n      ' + raw})
    parsed = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    schema = parsed.types_in(parsed.location)['Inline'].shape
    with pytest.raises(ValueError, match='root type name'):
        to_raml(schema)
    assert reparse(memory_workspace, to_raml(schema, name='Inline')).types['Inline'].validate({'next': {}}) is None


def test_external_definitions_and_recursion_keep_named_references(memory_workspace):
    text = export(
        memory_workspace,
        {
            'definitions': {'Node': {'$ref': 'node.json'}},
            'type': 'array',
            'items': {'$ref': '#/definitions/Node'},
        },
        **{
            'node.json': json.dumps(
                {
                    'type': 'object',
                    'properties': {'id': {'$ref': 'uuid.json'}, 'next': {'$ref': '#'}},
                    'required': ['id'],
                }
            ),
            'uuid.json': json.dumps({'type': 'string', 'pattern': '^u$'}),
        },
    )
    assert text.startswith('#%RAML 1.0 Library\n')
    types = yaml.safe_load(text.partition('\n')[2])['types']
    assert types['Node']['properties']['id'] == {'type': 'uuid'}
    assert list(types) == ['Node', 'uuid', 'schema']
    assert types['Node']['properties']['next'] == {'type': 'Node', 'required': False}
    assert types['schema']['items'] == {'type': 'Node'}
    parsed = reparse(memory_workspace, text)
    assert isinstance(parsed, Library)
    assert parsed.types['schema'].validate([{'id': 'u', 'next': {'id': 'u'}}]) is None
    assert parsed.types['schema'].validate([{'id': 'wrong'}]) is not None


def test_an_anonymous_constrained_union_member_is_not_silently_lost(memory_workspace):
    with pytest.raises(ValueError, match='constrained anonymous union member'):
        export(memory_workspace, {'anyOf': [{'type': 'string', 'minLength': 3}, {'type': 'integer'}]})


def test_unconstrained_union_round_trips(memory_workspace):
    text = export(memory_workspace, {'type': ['string', 'null']})
    assert yaml.safe_load(text.partition('\n')[2]) == {'type': 'string | nil'}
    parsed = reparse(memory_workspace, text)
    assert parsed.shape.validate('ok') is None
    assert parsed.shape.validate(None) is None


def test_root_name_collision_is_reported(memory_workspace):
    with pytest.raises(ValueError, match='root type name conflicts'):
        export(memory_workspace, {'definitions': {'schema': {'type': 'integer'}}, 'type': 'string'})


@pytest.mark.parametrize('required', [True, False], ids=['required', 'optional'])
def test_a_literal_question_mark_property_keeps_its_name_and_requiredness(memory_workspace, required):
    schema = {'type': 'object', 'properties': {'foo?': {'type': 'string'}}}
    if required:
        schema['required'] = ['foo?']
    text = export(memory_workspace, schema)
    assert yaml.safe_load(text.partition('\n')[2])['properties']['foo?']['required'] is required
    parsed = reparse(memory_workspace, text)
    properties = parsed.shape.shape.properties
    assert list(properties) == ['foo?']
    assert properties['foo?'].required is required
    assert (parsed.shape.validate({}) is None) is not required


@pytest.mark.parametrize('name', ['/^x/', '/^x/?'])
def test_a_literal_pattern_looking_property_cannot_be_exported(memory_workspace, name):
    with pytest.raises(ValueError, match='literal JSON Schema property name'):
        export(memory_workspace, {'type': 'object', 'properties': {name: {'type': 'string'}}})


def test_closed_object_with_pattern_properties_is_rejected_before_writing_invalid_raml(memory_workspace):
    with pytest.raises(ValueError, match='patternProperties with additionalProperties'):
        export(
            memory_workspace,
            {'type': 'object', 'patternProperties': {'^x': {'type': 'string'}}, 'additionalProperties': False},
        )


def test_pointer_escaped_definition_names_are_exported_without_collisions(memory_workspace):
    text = export(
        memory_workspace,
        {
            'definitions': {
                'A/B': {'type': 'string', 'minLength': 2},
                'A~B': {'type': 'integer', 'minimum': 1},
                'A_B': {'type': 'boolean'},
            },
            'type': 'object',
            'properties': {
                'slash': {'$ref': '#/definitions/A~1B'},
                'tilde': {'$ref': '#/definitions/A~0B'},
                'underscore': {'$ref': '#/definitions/A_B'},
            },
        },
    )
    types = yaml.safe_load(text.partition('\n')[2])['types']
    names = [types['schema']['properties'][key]['type'] for key in ('slash', 'tilde', 'underscore')]
    assert len(set(names)) == 3
    assert set(names) <= set(types)
    parsed = reparse(memory_workspace, text)
    shape = parsed.types['schema']
    assert shape.validate({'slash': 'ab', 'tilde': 2, 'underscore': True}) is None
    assert shape.validate({'slash': 'a', 'tilde': 0, 'underscore': True}) is not None


def test_distinct_external_targets_with_the_same_stem_remain_distinct(memory_workspace):
    text = export(
        memory_workspace,
        {
            'type': 'object',
            'properties': {'a': {'$ref': 'a/item.json'}, 'b': {'$ref': 'b/item.json'}},
            'required': ['a', 'b'],
        },
        **{'a/item.json': json.dumps({'type': 'string'}), 'b/item.json': json.dumps({'type': 'integer'})},
    )
    types = yaml.safe_load(text.partition('\n')[2])['types']
    assert list(types) == ['item', 'item2', 'schema']
    assert types['schema']['properties']['a'] == {'type': 'item'}
    assert types['schema']['properties']['b'] == {'type': 'item2'}
    shape = reparse(memory_workspace, text).types['schema']
    assert shape.validate({'a': 'one', 'b': 2}) is None
    assert shape.validate({'a': 2, 'b': 'one'}) is not None


def test_a_definition_named_like_a_builtin_is_given_a_referenceable_name(memory_workspace):
    text = export(
        memory_workspace,
        {
            'definitions': {'string': {'type': 'integer'}},
            'type': 'object',
            'properties': {'x': {'$ref': '#/definitions/string'}},
        },
    )
    types = yaml.safe_load(text.partition('\n')[2])['types']
    assert types['schema']['properties']['x']['type'] != 'string'
    shape = reparse(memory_workspace, text).types['schema']
    assert shape.validate({'x': 2}) is None
    assert shape.validate({'x': 'two'}) is not None


def test_reference_only_cycle_has_a_projection_diagnostic(memory_workspace):
    with pytest.raises(RamlError) as caught:
        export(
            memory_workspace,
            {
                'definitions': {'A': {'$ref': '#/definitions/B'}, 'B': {'$ref': '#/definitions/A'}},
                'type': 'string',
            },
        )
    assert caught.value.head.message == 'JSON schema construct has no RAML equivalent'
    assert caught.value.head.info == {'construct': 'reference-only cycle'}


def test_inline_library_needs_an_explicit_root_name(memory_workspace):
    raw = json.dumps({'definitions': {'Code': {'type': 'string'}}, 'type': 'object'})
    root = memory_workspace({'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  Inline:\n    type: |\n      ' + raw})
    parsed = memory_workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    shape = parsed.types_in(parsed.location)['Inline'].shape
    with pytest.raises(ValueError, match='root type name'):
        to_raml(shape)
    assert list(yaml.safe_load(to_raml(shape, name='Inline').partition('\n')[2])['types']) == ['Code', 'Inline']
