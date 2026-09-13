"""RAML -> `HTTPRoute`, and the MCP components that come out of it.

Two documents. `LIBRARY` is four endpoints, small enough that a test naming one
argument says what it means. The `sample` fixture is the project's worked
example, which exercises every construct the model carries -- a `queryString`,
two media types under one body, a templated `baseUri`, four `documentation:`
entries, security on every method -- and each of those is a thing this had to be
taught.
"""

from __future__ import annotations

import json
import pathlib

import httpx2
import pytest
from conftest import TENANT
from fastmcp import Client
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap
from fastmcp.utilities.openapi import HTTPRoute, ParameterInfo
from fastmcp.utilities.openapi.director import RequestDirector
from pyraml import ParseOptions, parse_from_path

from fastmcp_raml import NO_SPEC, RAMLProvider, base_url_of, documents_of, raml_mcp, to_http_routes

LIBRARY = """#%RAML 1.0
title: Library
baseUri: https://api.example/v1
types:
  Book:
    properties:
      isbn:
        type: string
        pattern: ^\\d{13}$
      pages?:
        type: integer
        minimum: 1
/books:
  get:
    displayName: Every book
    queryParameters:
      q?: string
    responses:
      200:
        body:
          application/json: Book[]
  post:
    body:
      application/json: Book
    responses:
      201:
        body:
          application/json: Book
  /{isbn}:
    uriParameters:
      isbn: string
    get:
      responses:
        200:
          body:
            application/json: Book
        404:
          description: no such book
"""


@pytest.fixture
def document(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / 'api.raml'
    source.write_text(LIBRARY, encoding='utf-8')
    return source


@pytest.fixture
def parsed(document: pathlib.Path):
    return parse_from_path(document, ParseOptions(unwrap=True, validate=True))


def raml(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    source = tmp_path / 'api.raml'
    source.write_text('#%RAML 1.0\ntitle: T\nbaseUri: https://api.example\n' + body, encoding='utf-8')
    return source


def transport() -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == 'POST':
            return httpx2.Response(201, json=json.loads(request.content))
        if request.url.path.endswith('/books'):
            return httpx2.Response(200, json=[{'isbn': '9' * 13}])
        return httpx2.Response(200, json={'isbn': request.url.path.rsplit('/', 1)[-1]})

    return httpx2.MockTransport(handler)


def client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(base_url='https://api.example/v1', transport=transport())


class TestRoutes:
    def test_every_operation_becomes_a_route(self, parsed):
        built = to_http_routes(parsed)
        assert {(r.method, r.path) for r in built.routes} == {
            ('GET', '/books'),
            ('POST', '/books'),
            ('GET', '/books/{isbn}'),
        }
        assert not built.dropped

    def test_ramls_three_parameter_places_become_locations(self, parsed):
        by_id = {r.operation_id: r for r in to_http_routes(parsed).routes}
        assert [(p.name, p.location) for p in by_id['get_books'].parameters] == [('q', 'query')]
        assert [(p.name, p.location) for p in by_id['get_books_isbn'].parameters] == [('isbn', 'path')]

    def test_a_uri_parameter_is_always_required(self, parsed):
        # It is part of the path: a request without it addresses nothing.
        route = next(r for r in to_http_routes(parsed).routes if r.path.endswith('{isbn}'))
        assert route.parameters[0].required is True

    def test_a_parameters_schema_carries_its_facets(self, parsed):
        route = next(r for r in to_http_routes(parsed).routes if r.method == 'POST')
        assert route.request_body is not None
        body = route.request_body.content_schema['application/json']
        assert body['properties']['isbn']['pattern'] == r'^\d{13}$'
        assert body['properties']['pages']['minimum'] == 1
        assert body['required'] == ['isbn']

    def test_responses_keep_their_codes_and_descriptions(self, parsed):
        route = next(r for r in to_http_routes(parsed).routes if r.path.endswith('{isbn}'))
        assert sorted(route.responses) == ['200', '404']
        assert route.responses['404'].description == 'no such book'

    def test_the_flat_schema_and_map_are_precomputed(self, parsed):
        by_id = {r.operation_id: r for r in to_http_routes(parsed).routes}
        route = by_id['post_books']
        # A body's properties are flattened into the tool's arguments.
        assert 'isbn' in route.flat_param_schema['properties']
        assert route.parameter_map['isbn']['location'] == 'body'
        assert by_id['get_books_isbn'].parameter_map['isbn']['location'] == 'path'

    def test_the_first_path_segment_becomes_a_tag(self, parsed):
        # RAML has no tags, and `RouteMap` selects on them.
        assert {r.tags[0] for r in to_http_routes(parsed).routes} == {'books'}

    def test_an_unflattened_document_is_refused(self, document):
        raml_document = parse_from_path(document, ParseOptions(unwrap=False))
        with pytest.raises(AssertionError, match='unwrapped'):
            to_http_routes(raml_document)

    def test_the_openapi_dialect_pass_is_not_selected(self, parsed):
        # These schemas are already JSON Schema. `openapi_version` would put
        # them through `convert_openapi_schema_to_json_schema`, which has
        # nothing to do to them and something to break.
        assert all(r.openapi_version is None for r in to_http_routes(parsed).routes)


class TestQueryString:
    """`queryString:` types the query as a whole, instead of `queryParameters:`."""

    def test_an_object_query_string_becomes_parameters(self, sample):
        route = next(r for r in to_http_routes(sample).routes if r.path == '/deliveries')
        assert [(p.name, p.location) for p in route.parameters] == [
            ('since', 'query'),
            ('status', 'query'),
            ('city', 'query'),
            ('carrier', 'query'),
        ]
        assert route.parameters[0].required is True
        assert route.parameters[1].required is False

    def test_its_members_keep_their_schemas(self, sample):
        route = next(r for r in to_http_routes(sample).routes if r.path == '/deliveries')
        by_name = {p.name: p for p in route.parameters}
        assert by_name['status'].schema_['enum'] == ['pending', 'sent', 'delivered']
        assert by_name['city'].description.startswith('Matched against the address')

    def test_a_query_string_that_is_not_an_object_is_reported(self, tmp_path):
        source = raml(tmp_path, 'types:\n  Q: string | integer\n/x:\n  get:\n    queryString: Q\n')
        built = to_http_routes(parse_from_path(source, ParseOptions(unwrap=True, validate=True)))
        assert built.routes[0].parameters == []
        assert any('queryString names no object type' in message for message in built.dropped)


class TestMediaTypes:
    def test_a_json_body_is_preferred_over_one_declared_first(self, tmp_path):
        # Both directions of the same declaration, because the point is that
        # the author's order does not decide it.
        for order in (
            'application/xml:\n        type: string\n      application/json:\n        type: string\n',
            'application/json:\n        type: string\n      application/xml:\n        type: string\n',
        ):
            source = raml(tmp_path, f'/x:\n  post:\n    body:\n      {order}')
            built = to_http_routes(parse_from_path(source, ParseOptions(unwrap=True, validate=True)))
            body = built.routes[0].request_body
            assert body is not None
            # `RequestDirector` and `_combine_schemas_and_map_params` both read
            # the first key, so it decides the encoding and the arguments.
            assert next(iter(body.content_schema)) == 'application/json'

    def test_two_media_types_for_one_body_are_both_kept(self, sample):
        route = next(r for r in to_http_routes(sample).routes if r.method == 'POST' and r.path == '/books')
        assert route.request_body is not None
        assert list(route.request_body.content_schema) == ['application/json', 'application/xml']

    def test_a_body_the_director_cannot_encode_is_reported(self, tmp_path):
        source = raml(
            tmp_path, '/x:\n  post:\n    body:\n      application/xml:\n        properties:\n          a: string\n'
        )
        built = to_http_routes(parse_from_path(source, ParseOptions(unwrap=True, validate=True)))
        assert any('is sent as JSON' in message for message in built.dropped)

    def test_a_streaming_response_is_reported_as_buffered(self, tmp_path):
        # FastMCP reads the whole response before returning; upstream lists
        # response streaming as unbuilt.
        source = raml(
            tmp_path,
            '/events:\n  get:\n    responses:\n      200:\n        body:\n          text/event-stream: string\n',
        )
        built = to_http_routes(parse_from_path(source, ParseOptions(unwrap=True, validate=True)))
        assert any('read to the end' in message for message in built.dropped)

    def test_a_multipart_body_keeps_its_media_type(self, tmp_path):
        source = raml(
            tmp_path,
            '/upload:\n  post:\n    body:\n      multipart/form-data:\n'
            '        properties:\n          caption: string\n          cover: file\n',
        )
        built = to_http_routes(parse_from_path(source, ParseOptions(unwrap=True, validate=True)))
        body = built.routes[0].request_body
        assert body is not None
        # The director dispatches on this key to build `files=` rather than JSON.
        assert next(iter(body.content_schema)) == 'multipart/form-data'
        assert sorted(built.routes[0].flat_param_schema['properties']) == ['caption', 'cover']
        # A `file` is carried as text, which is all an MCP argument can be.
        assert built.routes[0].flat_param_schema['properties']['cover']['type'] == 'string'
        assert not built.dropped

    async def test_a_multipart_body_is_sent_as_multipart(self, tmp_path):
        source = raml(
            tmp_path,
            '/upload:\n  post:\n    body:\n      multipart/form-data:\n'
            '        properties:\n          caption: string\n',
        )
        seen: dict[str, str] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen['content_type'] = request.headers['content-type']
            seen['body'] = request.content.decode('utf-8', 'replace')
            return httpx2.Response(200, json={'ok': True})

        mcp = raml_mcp(
            source,
            client=httpx2.AsyncClient(base_url='https://api.example', transport=httpx2.MockTransport(handler)),
        )
        async with Client(mcp) as connected:
            await connected.call_tool('post_upload', {'caption': 'a cover'})
        assert seen['content_type'].startswith('multipart/form-data')
        assert 'a cover' in seen['body']


class TestTheSample:
    """`fixtures/sample` -- every construct the model carries, in one document."""

    def test_every_operation_is_reached(self, sample):
        built = to_http_routes(sample)
        assert {(r.method, r.path) for r in built.routes} == {
            ('POST', '/books'),
            ('GET', '/books'),
            ('GET', '/books/{isbn}'),
            ('DELETE', '/books/{isbn}'),
            ('POST', '/shelves'),
            ('GET', '/deliveries'),
            ('GET', '/publications'),
            ('GET', '/search'),
        }

    def test_a_trait_reaches_the_arguments(self, sample):
        # `is: [paged]` on the `collection` resource type, two levels of
        # indirection from the method that ends up carrying it.
        route = next(r for r in to_http_routes(sample).routes if (r.method, r.path) == ('GET', '/books'))
        assert sorted(route.flat_param_schema['properties']) == ['limit', 'offset']

    def test_security_is_reported_once_for_the_document(self, sample):
        built = to_http_routes(sample)
        secured = [m for m in built.dropped if m.startswith('securedBy')]
        assert len(secured) == 1
        assert 'oauth2' in secured[0]
        assert 'basic' in secured[0]
        assert 'machineToken' in secured[0]

    def test_nothing_else_is_dropped(self, sample):
        assert [m for m in to_http_routes(sample).dropped if not m.startswith('securedBy')] == []

    def test_a_recursive_type_closes_through_defs(self, sample):
        route = next(r for r in to_http_routes(sample).routes if (r.method, r.path) == ('POST', '/books'))
        assert 'Book' in route.request_schemas
        assert route.flat_param_schema['properties']['related']['items'] == {'$ref': '#/$defs/Book'}


class TestTheBaseUri:
    def test_version_is_substituted_from_the_version_node(self, sample):
        assert base_url_of(sample) == 'https://{tenant}.books.example.com/v2'

    def test_a_supplied_parameter_is_substituted(self, sample):
        assert base_url_of(sample, TENANT) == 'https://acme.books.example.com/v2'

    def test_an_unbound_parameter_is_named_rather_than_blanked(self, sample):
        with pytest.raises(ValueError, match='leaves tenant unbound'):
            RAMLProvider(sample)

    def test_a_client_is_built_from_the_resolved_base_uri(self, sample):
        provider = RAMLProvider(sample, base_uri_parameters=TENANT)
        assert str(provider._client.base_url).rstrip('/') == 'https://acme.books.example.com/v2'
        # Upstream built it, so upstream's lifespan closes it.
        assert provider._owns_client is True

    def test_a_supplied_client_is_not_owned(self, sample):
        provider = RAMLProvider(sample, client=client(), base_uri_parameters=TENANT)
        assert provider._owns_client is False

    def test_a_document_without_a_base_uri_needs_a_client(self, tmp_path):
        source = tmp_path / 'api.raml'
        source.write_text('#%RAML 1.0\ntitle: T\n/x:\n  get:\n', encoding='utf-8')
        parsed_document = parse_from_path(source, ParseOptions(unwrap=True, validate=True))
        with pytest.raises(ValueError, match='no baseUri'):
            RAMLProvider(parsed_document)


class TestDocumentation:
    """RAML carries prose about the API that no schema states."""

    def test_the_entries_are_read(self, sample):
        assert [d.title for d in documents_of(sample)] == [
            'Getting started',
            'Authentication',
            'Pagination',
            'Errors',
        ]

    async def test_they_are_published_as_resources(self, sample_server):
        async with Client(sample_server) as connected:
            resources = {str(r.uri): r for r in await connected.list_resources()}
        assert 'docs://getting_started' in resources
        assert resources['docs://authentication'].mime_type == 'text/markdown'
        assert resources['docs://errors'].description == 'Errors'

    async def test_one_can_be_read(self, sample_server):
        async with Client(sample_server) as connected:
            contents = await connected.read_resource('docs://pagination')
        assert 'offset' in contents[0].text

    async def test_they_can_be_turned_off(self, sample_server_without_docs):
        async with Client(sample_server_without_docs) as connected:
            assert await connected.list_resources() == []

    async def test_the_api_description_becomes_the_servers_instructions(self, sample_server):
        assert sample_server.instructions is not None
        assert 'Markdown' in sample_server.instructions


class TestTheComponentLoop:
    """What `OpenAPIProvider` does after parsing, owned here rather than reused.

    Every one of these worked by inheritance once. They are the reason the loop
    can be owned: each names a feature that a copy would otherwise lose without
    saying so.
    """

    def test_the_director_reads_no_spec(self):
        # The fact the whole integration rests on. `RequestDirector.__init__`
        # stores its argument and `build()` never touches it, so a provider owes
        # it no document -- and if that ever stops being true, this fails rather
        # than a server.
        route = HTTPRoute(
            path='/books/{isbn}',
            method='GET',
            operation_id='get_book',
            parameters=[ParameterInfo(name='isbn', location='path', required=True, schema={'type': 'string'})],
            parameter_map={'isbn': {'location': 'path', 'openapi_name': 'isbn'}},
        )
        request = RequestDirector(NO_SPEC).build(route, {'isbn': '9' * 13}, 'https://api.example')
        assert str(request.url) == f'https://api.example/books/{"9" * 13}'

    async def test_route_maps_exclude(self, sample):
        provider = RAMLProvider(
            sample,
            base_uri_parameters=TENANT,
            route_maps=[RouteMap(methods=['DELETE'], mcp_type=MCPType.EXCLUDE)],
        )
        names = {t.name for t in await provider._list_tools()}
        assert 'delete_books_isbn' not in names
        # Named, so the assertion above cannot pass on an empty component set.
        assert 'get_books_isbn' in names

    async def test_a_route_map_can_make_a_resource(self, sample):
        provider = RAMLProvider(
            sample,
            base_uri_parameters=TENANT,
            route_maps=[RouteMap(pattern=r'^/publications$', mcp_type=MCPType.RESOURCE)],
        )
        uris = {str(r.uri) for r in await provider._list_resources()}
        assert 'resource://get_publications' in uris

    async def test_mcp_names_renames(self, sample):
        provider = RAMLProvider(
            sample,
            base_uri_parameters=TENANT,
            mcp_names={'get_search': 'find'},
        )
        assert 'find' in {t.name for t in await provider._list_tools()}

    async def test_the_component_callback_runs(self, sample):
        seen: list[str] = []
        provider = RAMLProvider(
            sample,
            base_uri_parameters=TENANT,
            mcp_component_fn=lambda route, component: seen.append(component.name),
        )
        assert sorted(seen) == sorted(t.name for t in await provider._list_tools())
        assert 'get_search' in seen

    async def test_the_route_map_callback_runs(self, sample):
        provider = RAMLProvider(
            sample,
            base_uri_parameters=TENANT,
            route_map_fn=lambda route, mcp_type: MCPType.EXCLUDE if route.method == 'POST' else None,
        )
        names = {t.name for t in await provider._list_tools()}
        assert not [name for name in names if name.startswith('post_')]
        assert 'get_books' in names

    async def test_tags_reach_the_components(self, sample):
        provider = RAMLProvider(sample, base_uri_parameters=TENANT, tags={'bookstore'})
        tools = {t.name: t for t in await provider._list_tools()}
        assert 'bookstore' in tools['get_search'].tags
        # And the tag the path supplied.
        assert 'search' in tools['get_search'].tags


class TestTheServer:
    async def test_every_operation_becomes_a_tool(self, document):
        mcp = raml_mcp(document, client=client())
        async with Client(mcp) as connected:
            names = {tool.name for tool in await connected.list_tools()}
        assert names == {'get_books', 'post_books', 'get_books_isbn'}

    async def test_a_tool_declares_the_arguments_the_raml_did(self, document):
        mcp = raml_mcp(document, client=client())
        async with Client(mcp) as connected:
            tools = {tool.name: tool for tool in await connected.list_tools()}
        assert list(tools['get_books'].input_schema['properties']) == ['q']
        assert sorted(tools['post_books'].input_schema['properties']) == ['isbn', 'pages']

    async def test_calling_a_tool_reaches_the_api(self, document):
        mcp = raml_mcp(document, client=client())
        async with Client(mcp) as connected:
            result = await connected.call_tool('get_books_isbn', {'isbn': '9' * 13})
        assert json.loads(result.content[0].text) == {'isbn': '9' * 13}

    async def test_a_body_is_rebuilt_from_flat_arguments(self, document):
        mcp = raml_mcp(document, client=client())
        async with Client(mcp) as connected:
            result = await connected.call_tool('post_books', {'isbn': '1' * 13, 'pages': 5})
        assert json.loads(result.content[0].text) == {'isbn': '1' * 13, 'pages': 5}

    async def test_the_server_takes_its_name_from_the_title(self, document):
        assert raml_mcp(document, client=client()).name == 'Library'

    def test_parse_options_reach_the_parser(self, sample_path, sample_options):
        # The sample includes from a sibling directory, so it parses only with
        # a workspace root wider than its own.
        mcp = raml_mcp(sample_path, options=sample_options, base_uri_parameters=TENANT)
        assert mcp.name == 'Bookstore API'

    def test_unwrap_and_validate_are_set_whatever_was_passed(self, sample_path, sample_options):
        bare = ParseOptions(workspace_root=sample_options.workspace_root)
        assert raml_mcp(sample_path, options=bare, base_uri_parameters=TENANT).name == 'Bookstore API'


def test_a_document_with_no_endpoints_yields_no_routes(tmp_path):
    source = tmp_path / 'api.raml'
    source.write_text('#%RAML 1.0\ntitle: T\ntypes:\n  A: string\n', encoding='utf-8')
    parsed_document = parse_from_path(source, ParseOptions(unwrap=True, validate=True))
    assert to_http_routes(parsed_document).routes == []
