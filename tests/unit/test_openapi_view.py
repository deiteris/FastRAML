"""`views/openapi.py` — an effective RAML API as OpenAPI 3.0.3."""

from __future__ import annotations

import pytest

from fastraml import OAS3Document, ParseOptions, parse_from_path, to_openapi
from fastraml.views.openapi import OAS3Schema


def converted(workspace, body: str):
    root = workspace({'api.raml': '#%RAML 1.0\n' + body})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    return to_openapi(raml)


def test_the_typed_model_keeps_an_explicit_null_distinct_from_omission():
    assert not hasattr(OAS3Document(), '__dict__')
    assert OAS3Document() != OAS3Document()
    assert OAS3Schema().to_dict() == {}
    assert OAS3Schema(default=None, example=None).to_dict() == {'default': None, 'example': None}


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
    assert path.parameters[0].to_dict() == {
        'name': 'storeId',
        'in': 'path',
        'required': True,
        'schema': {'type': 'string', 'description': 'Store ID'},
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
    declared = parse_from_path(root / 'api.raml')
    with pytest.raises(AssertionError, match='unwrapped model'):
        to_openapi(declared)

    fragment = parse_from_path(root / 'type.raml', ParseOptions(unwrap=True))
    with pytest.raises(TypeError, match='API fragment'):
        to_openapi(fragment)
