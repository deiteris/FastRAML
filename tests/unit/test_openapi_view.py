"""`views/openapi.py` — an effective RAML API as OpenAPI 3.0.3."""

from __future__ import annotations

import gc
import re
import weakref
from dataclasses import dataclass

import pytest
from jsonschema import Draft7Validator

from fastraml import OAS3Document, ParseOptions, to_openapi
from fastraml.types.values import DATETIME_ONLY_PATTERN, RFC2616_PATTERN, TIME_ONLY_PATTERN
from fastraml.views.openapi import OAS3Schema


def converted(workspace, body: str):
    root = workspace({'api.raml': '#%RAML 1.0\n' + body})
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    return to_openapi(raml)


def test_the_typed_model_keeps_an_explicit_null_distinct_from_omission():
    assert not hasattr(OAS3Document(), '__dict__')
    assert OAS3Document() != OAS3Document()
    assert OAS3Schema().to_dict() == {}
    assert OAS3Schema(default=None, example=None).to_dict() == {'default': None, 'example': None}


def test_serialization_does_not_keep_temporary_record_classes_alive():
    # Populate the parent first: inheriting its layout would omit new fields.
    OAS3Schema().to_dict()

    def serialize():
        @dataclass(slots=True, eq=False)
        class Temporary(OAS3Schema):
            label: str = ''

        return weakref.ref(Temporary), Temporary(type='string', label='extra').to_dict()

    reference, wire = serialize()
    gc.collect()
    assert reference() is None
    assert wire == {'type': 'string', 'label': 'extra'}


def test_metadata_server_documentation_and_paths(workspace):
    document, dropped = converted(
        workspace,
        """title: Catalog
description: The API
version: v2
baseUri: https://api.example.com/{version}
documentation:
  - title: Guide
    content: How to use it
/stores/{storeId}:
  displayName: Stores
  uriParameters:
    storeId:
      type: string
      description: Store ID
  get:
    queryParameters:
      limit?: integer
    headers:
      X-Trace: string
    responses:
      200:
        body:
          application/json: string[]
""",
    )

    assert isinstance(document, OAS3Document)
    assert document.openapi == '3.0.3'
    assert document.info.to_dict() == {'title': 'Catalog', 'version': 'v2', 'description': 'The API'}
    assert document.servers[0].variables['version'].default == 'v2'
    assert [tag.to_dict() for tag in document.tags] == [{'name': 'Guide', 'description': 'How to use it'}]
    path = document.paths['/stores/{storeId}']
    assert path.summary == 'Stores'
    # The description sits on the Parameter Object alone: OpenAPI has a place
    # for it there, and a second copy on the schema says the same thing twice.
    assert path.parameters[0].to_dict() == {
        'name': 'storeId',
        'in': 'path',
        'required': True,
        'schema': {'type': 'string'},
        'description': 'Store ID',
    }
    assert path.get is not None
    assert [(item.name, item.in_) for item in path.get.parameters] == [
        ('limit', 'query'),
        ('X-Trace', 'header'),
    ]
    assert path.get.responses['200'].description == 'OK'
    response_schema = path.get.responses['200'].content['application/json'].schema
    assert response_schema is not None
    assert response_schema.to_dict() == {
        'type': 'array',
        'items': {'type': 'string'},
    }
    assert dropped == []


def test_a_server_variable_without_a_default_gets_a_reported_placeholder(workspace):
    document, dropped = converted(
        workspace,
        'title: T\nbaseUri: https://{tenant}.example.com\nbaseUriParameters:\n  tenant: string\n',
    )
    assert document.servers[0].variables['tenant'].default == 'tenant'
    assert dropped == ["servers.variables.tenant: OpenAPI requires a default; used 'tenant' because RAML declared none"]


def test_a_typed_server_variable_default_is_stringified(workspace):
    """baseUriParameters are typed, so `default: 443` is an int, not a string."""
    document, dropped = converted(
        workspace,
        'title: T\n'
        'baseUri: https://{host}:{port}\n'
        'baseUriParameters:\n'
        '  host:\n'
        '    type: string\n'
        '    default: api.example.com\n'
        '  port:\n'
        '    type: integer\n'
        '    default: 443\n',
    )
    variables = document.servers[0].variables
    assert variables['host'].default == 'api.example.com'
    assert variables['port'].default == '443'
    assert dropped == []


def test_a_server_variable_default_with_no_string_form_is_reported(workspace):
    document, dropped = converted(
        workspace,
        'title: T\n'
        'baseUri: https://{tenant}.example.com\n'
        'baseUriParameters:\n'
        '  tenant:\n'
        '    type: array\n'
        '    items: string\n'
        '    default: [a, b]\n',
    )
    assert document.servers[0].variables['tenant'].default == 'tenant'
    assert dropped == [
        "servers.variables.tenant: RAML default ['a', 'b'] has no string form; used 'tenant'",
    ]


def test_named_types_are_lazy_components_and_recursion_closes(workspace):
    document, _ = converted(
        workspace,
        """title: Types
types:
  Unused: string
  Node:
    properties:
      value: string
      next?: Node
/nodes:
  post:
    body:
      application/json: Node
""",
    )

    operation = document.paths['/nodes'].post
    assert operation is not None
    assert operation.request_body is not None
    schema = operation.request_body.content['application/json'].schema
    assert isinstance(schema, OAS3Schema)
    assert schema.ref == '#/components/schemas/Node'
    assert list(document.components.schemas) == ['Node']
    assert document.components.schemas['Node'].properties['next'].ref == '#/components/schemas/Node'


@pytest.mark.parametrize(
    ('parent', 'narrowing', 'values'),
    [
        ('{type: string, maxLength: 10}', 'maxLength: 3', [('abc', True), ('abcd', False)]),
        ('{type: integer, minimum: 0}', 'minimum: 3', [(3, True), (2, False)]),
        ("{type: 'string[]', maxItems: 10}", 'maxItems: 1', [(['a'], True), (['a', 'b'], False)]),
        (
            '{type: array, items: {type: string, maxLength: 10}}',
            'items: {type: string, maxLength: 3}',
            [(['abc'], True), (['abcd'], False)],
        ),
        (
            '{type: object, properties: {name: string}, additionalProperties: false}',
            'properties: {age: integer}',
            [({'name': 'a', 'age': 1}, True), ({'name': 'a'}, False), ({'name': 'a', 'age': 1, 'x': 2}, False)],
        ),
        (
            '{type: object, properties: {name: {type: string, maxLength: 10}}}',
            'properties: {name: {type: string, maxLength: 3}}',
            [({'name': 'abc'}, True), ({'name': 'abcd'}, False)],
        ),
    ],
    ids=['string-bound', 'numeric-bound', 'array-bound', 'items', 'closed-object', 'property'],
)
def test_use_site_constraints_agree_with_raml(workspace, parent, narrowing, values):
    """A named parent must not hide effective constraints at a body use site."""
    root = workspace(
        {
            'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n'
            f'  Parent: {parent}\n'
            '/x:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n'
            f'            type: Parent\n            {narrowing}\n'
        }
    )
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    body = raml.endpoints['/x'].operations['get'].responses['200'].bodies['application/json'].shape
    document, dropped = to_openapi(raml)
    wire = document.to_dict()
    exported = wire['paths']['/x']['get']['responses']['200']['content']['application/json']['schema']
    # These cases use the common JSON Schema/OAS subset. Resolve OAS component
    # pointers against the actual document rather than an independently built schema.
    validator = Draft7Validator({'components': wire['components'], **exported})
    for value, expected in values:
        assert (body.validate(value) is None) is expected
        assert validator.is_valid(value) is expected
    assert dropped == []


def test_narrowing_on_parameters_headers_and_request_bodies(workspace):
    document, dropped = converted(
        workspace,
        'title: T\ntypes:\n  Text: {type: string, maxLength: 10}\n'
        '/x:\n  post:\n'
        '    queryParameters:\n      q: {type: Text, maxLength: 3}\n'
        '    headers:\n      X-Text: {type: Text, maxLength: 4}\n'
        '    body:\n      application/json: {type: Text, maxLength: 5}\n'
        '    responses:\n      200:\n        headers:\n          X-Result: {type: Text, maxLength: 6}\n',
    )
    operation = document.to_dict()['paths']['/x']['post']
    schemas = [
        operation['parameters'][0]['schema'],
        operation['parameters'][1]['schema'],
        operation['requestBody']['content']['application/json']['schema'],
        operation['responses']['200']['headers']['X-Result']['schema'],
    ]
    for schema, bound in zip(schemas, [3, 4, 5, 6], strict=True):
        validator = Draft7Validator(schema)
        assert validator.is_valid('a' * bound)
        assert not validator.is_valid('a' * (bound + 1))
    assert dropped == []


@pytest.mark.parametrize(
    'parent',
    [
        '{type: string, maxLength: 10}',
        '{type: array, items: {type: string, maxLength: 10}}',
        '{properties: {name: {type: string, maxLength: 10}}, additionalProperties: false}',
        '{type: string | integer}',
    ],
    ids=['scalar', 'nested-items', 'nested-property', 'union'],
)
def test_an_unchanged_use_site_keeps_a_bare_reference(workspace, parent):
    document, dropped = converted(
        workspace,
        f'title: T\ntypes:\n  Text: {parent}\n/x:\n  post:\n    body:\n      application/json: {{type: Text}}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema == {'$ref': '#/components/schemas/Text'}
    assert dropped == []


def test_narrowed_scalar_does_not_export_an_unused_parent_component(workspace):
    document, dropped = converted(
        workspace,
        'title: T\ntypes:\n  Text: {type: string, maxLength: 10}\n'
        '/x:\n  post:\n    body:\n      application/json: {type: Text, maxLength: 3}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema == {'type': 'string', 'maxLength': 3}
    assert document.components.schemas == {}
    assert dropped == []


@pytest.mark.parametrize(
    ('parent', 'members'),
    [
        (
            '{properties: {name: {type: string, maxLength: 10}}}',
            'properties: {name: {type: string, maxLength: 10}}',
        ),
        ('{type: array, items: {type: string, maxLength: 10}}', 'items: {type: string, maxLength: 10}'),
    ],
    ids=['property', 'items'],
)
def test_redeclared_equivalent_members_keep_a_reference(workspace, parent, members):
    """Distinct member objects need comparison, not an assumed structural change."""
    document, dropped = converted(
        workspace,
        f'title: T\ntypes:\n  Value: {parent}\n'
        '/x:\n  post:\n    body:\n'
        f'      application/json: {{type: Value, {members}}}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema == {'$ref': '#/components/schemas/Value'}
    assert dropped == []


@pytest.mark.parametrize(
    ('parent', 'members', 'expected'),
    [
        ('name: string', '{name: {type: string, description: Who}}', {'type': 'string', 'description': 'Who'}),
        ('name?: string', '{name: string}', {'type': 'string'}),
    ],
    ids=['member-metadata', 'member-requiredness'],
)
def test_a_member_difference_beyond_facets_inlines_the_use_site(workspace, parent, members, expected):
    """Member equivalence covers everything the member projects, not only its kind."""
    document, dropped = converted(
        workspace,
        f'title: T\ntypes:\n  P:\n    properties:\n      {parent}\n'
        f'/x:\n  post:\n    body:\n      application/json: {{type: P, properties: {members}}}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema['properties']['name'] == expected
    assert schema['required'] == ['name']
    assert dropped == []


def test_an_equivalence_check_reports_no_loss_for_output_it_discards(workspace):
    """The use site is a `$ref`, so only the component's path may carry the notice."""
    document, dropped = converted(
        workspace,
        'title: T\ntypes:\n  P:\n    properties:\n      f: {type: file, fileTypes: [image/png]}\n'
        '/x:\n  post:\n    body:\n      application/json:\n        type: P\n'
        '        properties:\n          f: {type: file, fileTypes: [image/png]}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema == {'$ref': '#/components/schemas/P'}
    assert [notice.partition(':')[0] for notice in dropped] == ['components.schemas.P.f']


def test_a_narrowed_member_does_not_export_an_unused_parent_component(workspace):
    document, dropped = converted(
        workspace,
        'title: T\ntypes:\n  P:\n    properties:\n      name: {type: string, maxLength: 10}\n'
        '/x:\n  post:\n    body:\n      application/json:\n        type: P\n'
        '        properties:\n          name: {type: string, maxLength: 3}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema['properties']['name'] == {'type': 'string', 'maxLength': 3}
    assert document.components.schemas == {}
    assert dropped == []


def test_metadata_and_enum_refinements_compose_with_a_reference(workspace):
    document, dropped = converted(
        workspace,
        'title: T\ntypes:\n  Text: {type: string, maxLength: 3, enum: [abc, ab]}\n'
        '/x:\n  post:\n    body:\n'
        '      application/json: {type: Text, description: Narrow choice, enum: [ab]}\n',
    )
    wire = document.to_dict()
    schema = wire['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert schema == {
        'allOf': [{'$ref': '#/components/schemas/Text'}],
        'description': 'Narrow choice',
        'enum': ['ab'],
    }
    validator = Draft7Validator({'components': wire['components'], **schema})
    assert validator.is_valid('ab')
    assert not validator.is_valid('abc')
    assert dropped == []


@pytest.mark.parametrize(
    ('inherited', 'supplied', 'expected'),
    [('1', 'true', True), ('[1]', '[true]', [True]), ('{value: 1}', '{value: true}', {'value': True})],
    ids=['scalar', 'array', 'object'],
)
def test_reference_defaults_keep_boolean_values_distinct_from_numbers(workspace, inherited, supplied, expected):
    document, dropped = converted(
        workspace,
        f'title: T\ntypes:\n  Value: {{type: any, default: {inherited}}}\n'
        '/x:\n  post:\n    body:\n'
        f'      application/json: {{type: Value, default: {supplied}}}\n',
    )
    schema = document.to_dict()['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    assert Draft7Validator({'const': expected}).is_valid(schema['default'])
    assert schema['allOf'] == [{'$ref': '#/components/schemas/Value'}]
    assert dropped == []


def test_narrowed_recursive_use_site_retains_its_head_reference(workspace):
    root = workspace(
        {
            'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n'
            '  Node:\n    properties:\n      name: {type: string, maxLength: 10}\n      next?: Node\n'
            '/x:\n  post:\n    body:\n      application/json:\n        type: Node\n'
            '        properties:\n          name: {type: string, maxLength: 3}\n'
        }
    )
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    shape = raml.endpoints['/x'].operations['post'].request.bodies['application/json'].shape
    document, dropped = to_openapi(raml)
    wire = document.to_dict()
    schema = wire['paths']['/x']['post']['requestBody']['content']['application/json']['schema']
    validator = Draft7Validator({'components': wire['components'], **schema})
    for value, expected in [
        ({'name': 'abc', 'next': {'name': 'longer'}}, True),
        ({'name': 'abcd'}, False),
        ({'name': 'abc', 'next': {'name': 'a' * 11}}, False),
    ]:
        assert (shape.validate(value) is None) is expected
        assert validator.is_valid(value) is expected
    assert dropped == []


def test_a_narrowed_property_heading_a_cycle_gets_a_valid_component_key(workspace):
    """`next?` heads the cycle its own `next` closes; OpenAPI keys exclude `?`."""
    root = workspace(
        {
            'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n'
            '  Node:\n    properties:\n      name: {type: string, maxLength: 10}\n'
            '      next?:\n        type: Node\n        properties:\n          name: {type: string, maxLength: 3}\n'
            '/x:\n  get:\n    responses:\n      200:\n        body:\n          application/json: Node\n'
        }
    )
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    shape = raml.types_in(raml.location)['Node']
    document, dropped = to_openapi(raml)
    wire = document.to_dict()
    assert all(re.fullmatch(r'[a-zA-Z0-9._-]+', key) for key in wire['components']['schemas'])
    schema = wire['paths']['/x']['get']['responses']['200']['content']['application/json']['schema']
    validator = Draft7Validator({'components': wire['components'], **schema})
    for value, expected in [
        ({'name': 'a' * 10, 'next': {'name': 'abc', 'next': {'name': 'abc'}}}, True),
        ({'name': 'a', 'next': {'name': 'abcd'}}, False),
        ({'name': 'a', 'next': {'name': 'abc', 'next': {'name': 'abcd'}}}, False),
    ]:
        assert (shape.validate(value) is None) is expected
        assert validator.is_valid(value) is expected
    assert dropped == []


def test_nullable_union_and_query_string(workspace):
    document, _ = converted(
        workspace,
        """title: Query
/search:
  get:
    queryString:
      properties:
        q: string
        page?: integer
    responses:
      200:
        body:
          application/json:
            properties:
              result?: string | nil
""",
    )

    operation = document.paths['/search'].get
    assert operation is not None
    assert [(item.name, item.required) for item in operation.parameters] == [('q', True), ('page', False)]
    schema = operation.responses['200'].content['application/json'].schema
    assert schema is not None
    result = schema.properties['result']
    assert result.type == 'string'
    assert result.nullable is True


def test_non_object_query_string_is_preserved_as_an_extension(workspace):
    document, _ = converted(workspace, 'title: Query\n/search:\n  get:\n    queryString: string\n')
    operation = document.paths['/search'].get
    assert operation is not None
    assert operation.extensions['x-query-string'] == {'type': 'string'}


def test_an_external_json_schema_is_projected_to_the_oas30_dialect(workspace):
    document, _ = converted(
        workspace,
        """title: Schema
types:
  Maybe: '{"type":["string","null"]}'
/x:
  get:
    responses:
      200:
        body:
          application/json: Maybe
""",
    )
    schema = document.components.schemas['Maybe']
    assert schema.type == 'string'
    assert schema.nullable is True


def test_a_projected_json_schema_keeps_the_raml_metadata(workspace):
    document, _ = converted(
        workspace,
        """title: Schema
types:
  Code:
    type: '{"type":"string"}'
    displayName: Code
    description: A code.
    example: abc
/x:
  get:
    responses:
      200:
        body:
          application/json: Code
""",
    )
    schema = document.components.schemas['Code']
    assert (schema.type, schema.title, schema.description, schema.example) == ('string', 'Code', 'A code.', 'abc')


@pytest.mark.parametrize(
    ('declared', 'pattern'),
    [
        ('time-only', TIME_ONLY_PATTERN),
        ('datetime-only', DATETIME_ONLY_PATTERN),
        ('\n    type: datetime\n    format: rfc2616', RFC2616_PATTERN),
    ],
    ids=['time-only', 'datetime-only', 'rfc2616'],
)
def test_a_date_time_without_an_openapi_format_is_the_parsers_pattern(workspace, declared, pattern):
    # OpenAPI 3.0 has no `time` format, and JSON Schema's `time` needs an
    # offset `time-only` never has; the pattern is the parser's own reading.
    used = '/x:\n  get:\n    responses:\n      200:\n        body:\n          application/json: T\n'
    document, _ = converted(workspace, f'title: Times\ntypes:\n  T: {declared}\n{used}')
    schema = document.components.schemas['T']
    assert (schema.type, schema.format, schema.pattern) == ('string', '', pattern)


def test_security_schemes_and_narrowed_scopes(workspace):
    document, dropped = converted(
        workspace,
        """title: Secure
securitySchemes:
  basic:
    type: Basic Authentication
  oauth:
    type: OAuth 2.0
    settings:
      authorizationUri: https://auth.example/authorize
      accessTokenUri: https://auth.example/token
      authorizationGrants: [authorization_code]
      scopes: [read, write]
/private:
  get:
    securedBy:
      - oauth:
          scopes: [read]
/public:
  get:
    securedBy: [null]
""",
    )

    schemes = document.components.security_schemes
    assert schemes['basic'].to_dict() == {'type': 'http', 'scheme': 'basic'}
    assert schemes['oauth'].flows is not None
    assert schemes['oauth'].flows.authorization_code is not None
    assert schemes['oauth'].flows.authorization_code.token_url == 'https://auth.example/token'
    private = document.paths['/private'].get
    public = document.paths['/public'].get
    assert private is not None
    assert private.security == [{'oauth': ['read']}]
    assert public is not None
    assert public.security == [{}]
    assert dropped == []


def test_a_scheme_declared_with_the_name_null_is_a_named_requirement(workspace):
    # Only a `null` entry (no scheme) is the anonymous `{}` requirement; a real
    # scheme whose name is the string 'null' must not open the operation.
    document, _ = converted(
        workspace,
        """title: Secure
securitySchemes:
  'null':
    type: Basic Authentication
/private:
  get:
    securedBy: ['null']
""",
    )
    operation = document.paths['/private'].get
    assert operation is not None
    assert operation.security == [{'null': []}]


def test_explicit_empty_security_overrides_global_security(workspace):
    document, _ = converted(
        workspace,
        """title: Secure
securitySchemes:
  basic:
    type: Basic Authentication
securedBy: [basic]
/public:
  get:
    securedBy: []
""",
    )
    operation = document.paths['/public'].get
    assert operation is not None
    assert operation.security == []
    assert operation.to_dict()['security'] == []


def test_annotations_become_extensions_and_loss_is_reported(workspace):
    document, dropped = converted(
        workspace,
        """title: Extended
annotationTypes:
  audience: string
(audience): public
securitySchemes:
  pass:
    type: Pass Through
/x:
  get:
    (audience): internal
""",
    )

    assert document.extensions['x-audience'] == 'public'
    operation = document.paths['/x'].get
    assert operation is not None
    assert operation.extensions['x-audience'] == 'internal'
    assert document.components.security_schemes['pass'].extensions['x-raml-type'] == 'Pass Through'
    assert any('Pass Through' in message for message in dropped)


def test_an_unwrapped_model_and_an_api_are_required(workspace):
    root = workspace(
        {
            'api.raml': '#%RAML 1.0\ntitle: T\n',
            'type.raml': '#%RAML 1.0 DataType\ntype: string\n',
        }
    )
    declared = workspace.parse(root / 'api.raml')
    with pytest.raises(AssertionError, match='unwrapped model'):
        to_openapi(declared)

    fragment = workspace.parse(root / 'type.raml', ParseOptions(unwrap=True))
    with pytest.raises(TypeError, match='API fragment'):
        to_openapi(fragment)
