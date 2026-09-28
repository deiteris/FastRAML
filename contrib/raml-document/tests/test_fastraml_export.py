"""FastRAML's schema export uses the same declaration spelling as this authoring model."""

from __future__ import annotations

import json

import yaml
from fastraml import ParseOptions, parse_from_string, to_raml

from raml_document import TypeDecl


def converted(schema: dict, tmp_path) -> tuple[str, dict]:
    raw = json.dumps(schema)
    source = '#%RAML 1.0 DataType\ntype: |\n' + ''.join(f'  {line}\n' for line in raw.splitlines())
    raml = parse_from_string(source, file_name='input.raml', base_dir=tmp_path, options=ParseOptions(unwrap=True))
    text = to_raml(raml.entry_point.shape.shape, name='Root')
    return text.partition('\n')[0], yaml.safe_load(text.partition('\n')[2])


def test_a_data_type_spells_facets_like_type_decl(tmp_path):
    header, declaration = converted(
        {'type': 'string', 'description': 'a code', 'pattern': '^x', 'minLength': 2, 'maxLength': 4}, tmp_path
    )
    assert header == '#%RAML 1.0 DataType'
    assert (
        declaration == TypeDecl(type='string', description='a code', pattern='^x', min_length=2, max_length=4).render()
    )


def test_a_library_spells_named_types_and_optional_properties_like_type_decl(tmp_path):
    header, document = converted(
        {
            'definitions': {'Code': {'type': 'string', 'minLength': 2}},
            'type': 'object',
            'properties': {'code': {'$ref': '#/definitions/Code'}},
        },
        tmp_path,
    )
    assert header == '#%RAML 1.0 Library'
    assert document['types']['Code'] == TypeDecl(type='string', min_length=2).render()
    assert document['types']['Root']['properties']['code'] == TypeDecl(type='Code', required=False).render()
