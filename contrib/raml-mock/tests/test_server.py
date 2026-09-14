from __future__ import annotations

import aiohttp
from aiohttp import FormData, web
from aiohttp.multipart import MultipartReader
from aiohttp.test_utils import TestClient, TestServer

from raml_mock import MockRequest, create_app, mock_server


class TestRouting:
    async def test_literal_route_precedes_parameter(self, client):
        response = await client.get('/items/fixed')
        assert response.status == 200
        assert await response.json() == 'fixed'

    async def test_missing_route_and_wrong_method_are_distinct(self, client):
        assert (await client.get('/missing')).status == 404
        response = await client.patch('/items/1')
        assert response.status == 405
        assert response.headers['Allow'] == 'DELETE, GET, HEAD, POST, PUT'

    async def test_path_and_query_values_are_validated(self, client):
        response = await client.get('/items/nope?enabled=yes')
        assert response.status == 400
        issues = (await response.json())['issues']
        assert {issue['location'] for issue in issues} == {'path.id', 'query.enabled'}

    async def test_repeated_query_values_form_an_array(self, source):
        seen = None

        async def inspect(request: MockRequest) -> web.Response:
            nonlocal seen
            seen = request.values
            return web.Response(status=204)

        app = create_app(source, overrides={('GET', '/items/{id}'): inspect})
        local = TestClient(TestServer(app))
        await local.start_server()
        try:
            response = await local.get('/items/7?tag=a&tag=b&enabled=true', headers={'X-Trace': 'trace'})
        finally:
            await local.close()
        assert response.status == 204
        assert seen.path == {'id': 7}
        assert seen.query == {'enabled': True, 'tag': ['a', 'b']}
        assert seen.headers == {'X-Trace': 'trace'}

    async def test_date_shapes_remain_text_without_type_string_dispatch(self, source):
        seen = None

        def inspect(request: MockRequest) -> web.Response:
            nonlocal seen
            seen = request.values.query
            return web.Response(status=204)

        local = TestClient(TestServer(create_app(source, overrides={('GET', '/items/{id}'): inspect})))
        await local.start_server()
        try:
            response = await local.get('/items/1?at=2000-01-01T00:00:00Z&date=2000-01-01')
        finally:
            await local.close()
        assert response.status == 204
        assert seen == {'enabled': True, 'at': '2000-01-01T00:00:00Z', 'date': '2000-01-01'}

    async def test_path_values_are_decoded_exactly_once(self, source):
        seen = []

        def inspect(request: MockRequest) -> web.Response:
            seen.append(request.values.path['value'])
            return web.Response(status=204)

        app = create_app(
            source,
            overrides={('GET', '/raw/{value}'): inspect, ('GET', '/reserved/{+value}'): inspect},
        )
        local = TestClient(TestServer(app))
        await local.start_server()
        try:
            encoded_separator = await local.get('/raw/a%2Fb')
            encoded_percent = await local.get('/raw/a%252Fb')
            reserved = await local.get('/reserved/a%2Fb')
        finally:
            await local.close()
        assert encoded_separator.status == 404
        assert encoded_percent.status == 204
        assert reserved.status == 204
        assert seen == ['a%2Fb', 'a/b']


class TestRequests:
    async def test_a_json_body_number_keeps_every_digit_the_client_sent(self, source):
        # The encoder already used simplejson with `use_decimal`; the decoder
        # used the standard library, so a number arrived as a float and came
        # back rounded. Query and header numbers were always Decimal.
        seen = []

        def inspect(request: MockRequest) -> web.Response:
            seen.append(request.values.body)
            return web.Response(status=204)

        local = TestClient(TestServer(create_app(source, overrides={('POST', '/items/{id}'): inspect})))
        await local.start_server()
        sent = '1.2345678901234567890123'
        try:
            response = await local.post(
                '/items/7',
                data=f'{{"id": 7, "name": "Dune", "size": {sent}}}',
                headers={'Content-Type': 'application/json'},
            )
        finally:
            await local.close()
        assert response.status == 204
        assert str(seen[0]['size']) == sent

    async def test_omitted_parameters_and_body_fields_receive_defaults(self, source):
        seen = []

        def inspect(request: MockRequest) -> web.Response:
            seen.append(request.values)
            return web.Response(status=204)

        app = create_app(
            source,
            overrides={('GET', '/items/{id}'): inspect, ('POST', '/items/{id}'): inspect},
        )
        local = TestClient(TestServer(app))
        await local.start_server()
        try:
            query_response = await local.get('/items/7')
            body_response = await local.post('/items/7', json={'id': 7, 'name': 'Dune'})
        finally:
            await local.close()
        assert query_response.status == body_response.status == 204
        assert seen[0].query == {'enabled': True}
        assert seen[0].headers == {'X-Trace': 'generated-trace'}
        assert seen[1].body == {'id': 7, 'name': 'Dune', 'category': 'book'}

    async def test_json_body_is_validated(self, client):
        good = await client.post('/items/2', json={'id': 2, 'name': 'Dune'})
        assert good.status == 201
        bad = await client.post('/items/2', json={'name': 'Dune'})
        assert bad.status == 400
        assert (await bad.json())['issues'][0]['location'] == 'body'

    async def test_malformed_json_is_reported(self, client):
        response = await client.post('/items/2', data='{', headers={'Content-Type': 'application/json'})
        assert response.status == 400
        assert (await response.json())['issues'][0]['message'] == 'body is not valid JSON'

    async def test_non_json_numeric_constants_are_rejected(self, client):
        for constant in ('NaN', 'Infinity', '-Infinity'):
            response = await client.post('/any', data=constant, headers={'Content-Type': 'application/json'})
            assert response.status == 400
            assert (await response.json())['issues'][0]['message'] == 'body is not valid JSON'

    async def test_form_fields_are_coerced(self, client):
        response = await client.post('/form', data={'count': '2', 'enabled': 'true'})
        assert response.status == 200
        assert await response.text() in {'count=2&enabled=true', 'enabled=true&count=2'}

    async def test_multipart_file_is_bytes(self, source):
        seen = None

        def inspect(request: MockRequest) -> web.Response:
            nonlocal seen
            seen = request.values.body
            return web.Response(status=204)

        local = TestClient(TestServer(create_app(source, overrides={('POST', '/upload'): inspect})))
        await local.start_server()
        form = FormData()
        form.add_field('note', 'cover')
        form.add_field('data', b'png', filename='cover.png', content_type='image/png')
        try:
            response = await local.post('/upload', data=form)
        finally:
            await local.close()
        assert response.status == 204
        assert seen == {'note': 'cover', 'data': b'png'}

    async def test_multipart_file_type_is_enforced(self, client):
        form = FormData()
        form.add_field('note', 'cover')
        form.add_field('data', b'gif', filename='cover.gif', content_type='image/gif')
        response = await client.post('/upload', data=form)
        assert response.status == 400
        assert (await response.json())['issues'][0]['location'] == 'body.data'

    async def test_binary_file_constraints_are_enforced(self, client):
        bad = await client.post('/binary', data=b'x', headers={'Content-Type': 'application/octet-stream'})
        assert bad.status == 400
        good = await client.post('/binary', data=b'xy', headers={'Content-Type': 'application/octet-stream'})
        assert good.status == 200
        assert await good.read() == b'\x00\x00\x00'


class TestResponses:
    async def test_a_type_example_is_used_when_the_body_has_none(self, client):
        response = await client.get('/items/3')
        assert response.status == 200
        assert await response.json() == {'id': 7, 'name': 'Dune'}
        assert response.headers['X-Generated'] == 'string'

    async def test_an_endpoint_example_precedes_its_type_examples(self, client):
        response = await client.get('/items/endpoint-example')
        assert response.status == 200
        assert await response.json() == {'id': 9, 'name': 'Endpoint'}

    async def test_a_named_example_can_be_selected_from_the_parent_type(self, client):
        response = await client.get('/items/3', headers={'X-RAML-Mock-Example': 'alternate'})
        assert response.status == 200
        assert await response.json() == {'id': 8, 'name': 'Foundation'}

    async def test_an_unconstrained_array_uses_its_item_type_example(self, client):
        response = await client.get('/items')
        assert response.status == 200
        assert await response.json() == [
            {'id': 7, 'name': 'Dune'},
            {'id': 8, 'name': 'Foundation'},
        ]

    async def test_an_ancestor_example_must_satisfy_the_effective_shape(self, client):
        response = await client.get('/items/narrow')
        assert response.status == 200
        assert await response.json() == {'id': 10, 'name': 'string'}

    async def test_automatic_selection_skips_a_non_strict_invalid_example(self, client):
        response = await client.get('/items/example-choice')
        assert response.status == 200
        assert await response.json() == {'id': 8}

    async def test_declared_status_and_named_example_can_be_selected(self, client):
        response = await client.get(
            '/items/3',
            headers={'X-RAML-Mock-Status': '404', 'X-RAML-Mock-Example': 'missing'},
        )
        assert response.status == 404
        assert await response.json() == {'id': 0, 'name': 'missing'}

    async def test_accept_negotiation_and_text(self, client):
        assert await (await client.get('/text', headers={'Accept': 'text/*'})).text() == 'hello'
        unacceptable = await client.get('/text', headers={'Accept': 'application/json'})
        assert unacceptable.status == 406

    async def test_specific_accept_exclusion_overrides_a_wildcard(self, client):
        excluded = await client.get('/items/3', headers={'Accept': 'application/json;q=0, */*;q=1'})
        assert excluded.status == 406

    async def test_scalar_text_uses_ramls_spelling_of_a_boolean(self, client):
        # `str(False)` is `'False'`, which is Python. Every other encoder here
        # lowercases; this one did not, and put a Python repr on the wire.
        assert await (await client.get('/scalar-text')).text() == 'false'

    async def test_a_fractional_number_can_be_written_as_text(self, client):
        # Generation produces `Decimal` for any non-integral number, which the
        # scalar encoder did not accept -- so this answered 500 rather than 200.
        response = await client.get('/scalar-text/number')
        assert response.status == 200
        assert await response.text() == '0.5'

    async def test_head_reports_the_headers_get_would_have_sent(self, client):
        # RFC 9110 section 9.3.2. Returning early on HEAD sent neither
        # `Content-Length` nor `Content-Type`; aiohttp drops the payload itself.
        get = await client.get('/items/3')
        head = await client.request('HEAD', '/items/3')
        assert head.status == get.status
        assert head.headers['Content-Length'] == get.headers['Content-Length']
        assert head.headers['Content-Type'] == get.headers['Content-Type']
        assert await head.read() == b''

    async def test_exact_decimal_generation_never_round_trips_through_float(self, client):
        response = await client.get('/decimal')
        assert response.status == 200
        assert await response.text() == '1.1'

        precise = await client.get('/precise')
        assert precise.status == 200
        assert await precise.text() == '1.123456789012345678901234567891'

    async def test_negative_upper_bounds_are_synthesized(self, client):
        integer = await client.get('/negative-integer')
        number = await client.get('/negative-number')
        assert await integer.json() == -5
        assert await number.json() == -5.5

    async def test_unique_arrays_generate_distinct_items(self, client):
        response = await client.get('/unique')
        assert response.status == 200
        assert await response.json() == [0, 1]

        defaulted = await client.get('/unique-default')
        assert defaulted.status == 200
        assert await defaulted.json() == ['preferred', 'string']

    async def test_min_properties_uses_typed_pattern_properties(self, client):
        response = await client.get('/pattern-object')
        assert response.status == 200
        assert await response.json() == {'x-mock0': 5, 'x-mock1': 6}

    async def test_common_constrained_string_patterns_are_synthesized(self, client):
        response = await client.get('/pattern-string')
        assert response.status == 200
        assert await response.json() == '0000000000000'

    async def test_validation_status_can_follow_an_apis_declared_error_semantics(self, source):
        def status(route, error):
            assert route.path == '/items/{id}'
            assert error.issues[0].location == 'body'
            return 422

        local = TestClient(TestServer(create_app(source, validation_status=status)))
        await local.start_server()
        try:
            response = await local.post('/items/2', json={'name': 'Dune'})
        finally:
            await local.close()
        assert response.status == 422

    async def test_multipart_response_uses_file_metadata(self, client):
        response = await client.get('/multipart-response')
        assert response.status == 200
        reader = MultipartReader.from_response(response)
        note = await reader.next()
        assert note is not None
        assert await note.text() == 'cover'
        data = await reader.next()
        assert data is not None
        assert await data.read() == b'png'
        assert data.filename == 'data'
        assert data.headers['Content-Type'] == 'image/png'

    async def test_file_types_apply_to_response_media(self, client):
        mismatch = await client.get('/bad-file-response')
        assert mismatch.status == 406
        json_mismatch = await client.get('/bad-json-file-response')
        assert json_mismatch.status == 406
        wildcard = await client.get('/wildcard-file-response')
        assert wildcard.status == 200
        assert await wildcard.read() == b'\x00'

    async def test_multipart_field_names_cannot_inject_headers(self, client):
        response = await client.get('/unsafe-multipart')
        assert response.status == 500
        assert (await response.json())['error'] == 'mock generation failed'

    async def test_xml_scalar_passes_through_but_structured_xml_does_not(self, client):
        scalar = await client.get('/xml', headers={'Accept': 'application/xml'})
        assert await scalar.text() == '<ok/>'
        structured = await client.get('/structured-xml', headers={'Accept': 'application/xml'})
        assert structured.status == 406


async def test_context_manager_runs_on_a_real_ephemeral_socket(source):
    async with mock_server(source) as server, aiohttp.ClientSession(server.url) as session:
        response = await session.get('/text')
        assert response.status == 200
        assert await response.text() == 'hello'
