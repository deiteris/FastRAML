from __future__ import annotations

import asyncio
import base64
import math

import pytest
from aiohttp.test_utils import TestClient, TestServer

from raml_mock import (
    AuthDecision,
    Authentication,
    BasicCredentials,
    BearerToken,
    GenerationOptions,
    MockOptions,
    RouteBehavior,
    StatefulResource,
    create_app,
    state_of,
)


async def connected(source, options: MockOptions) -> TestClient:
    client = TestClient(TestServer(create_app(source, mock_options=options)))
    await client.start_server()
    return client


class TestConfiguredBehavior:
    async def test_seed_is_stable_and_route_local(self, source):
        options = MockOptions(generation=GenerationOptions(seed='suite', collection_size=3))
        first = await connected(source, options)
        second = await connected(source, options)
        different = await connected(
            source,
            MockOptions(generation=GenerationOptions(seed='other', collection_size=3)),
        )
        try:
            one = await (await first.get('/generated-list')).json()
            await first.get('/optional')
            repeated = await (await first.get('/generated-list')).json()
            two = await (await second.get('/generated-list')).json()
            other = await (await different.get('/generated-list')).json()
        finally:
            await first.close()
            await second.close()
            await different.close()
        assert one == repeated == two
        assert one != other
        assert len(one) == 3

    async def test_optional_probability_is_explicit(self, source):
        client = await connected(
            source,
            MockOptions(generation=GenerationOptions(seed=1, optional_probability=1.0)),
        )
        try:
            value = await (await client.get('/optional')).json()
        finally:
            await client.close()
        assert set(value) == {'id', 'note'}

    async def test_collection_size_applies_when_items_are_implicit(self, source):
        client = await connected(source, MockOptions(generation=GenerationOptions(collection_size=3)))
        try:
            value = await (await client.get('/implicit-items')).json()
        finally:
            await client.close()
        assert value == [None, None, None]

    def test_non_finite_generation_and_delay_options_are_rejected(self):
        for value in (math.inf, -math.inf, math.nan):
            with pytest.raises(ValueError, match='finite'):
                RouteBehavior(delay=value)
            with pytest.raises(ValueError, match='between'):
                GenerationOptions(optional_probability=value)

    async def test_route_status_and_example_need_no_control_headers(self, source):
        options = MockOptions(
            routes={('GET', '/items/{id}'): RouteBehavior(status='404', example='missing')},
        )
        client = await connected(source, options)
        try:
            response = await client.get('/items/3')
            value = await response.json()
        finally:
            await client.close()
        assert response.status == 404
        assert value == {'id': 0, 'name': 'missing'}

    def test_route_status_must_be_declared(self, source):
        options = MockOptions(routes={('GET', '/items/{id}'): RouteBehavior(status='418')})
        with pytest.raises(ValueError, match='configured status is not declared'):
            create_app(source, mock_options=options)

    async def test_defaults_do_not_turn_a_valid_object_invalid(self, source):
        client = await connected(source, MockOptions())
        try:
            response = await client.post('/default-max', json={})
        finally:
            await client.close()
        assert response.status == 204

    async def test_a_delay_is_honoured_before_the_response(self, source):
        options = MockOptions(routes={('GET', '/items/{id}'): RouteBehavior(delay=0.05)})
        client = await connected(source, options)
        try:
            started = asyncio.get_running_loop().time()
            response = await client.get('/items/3')
            elapsed = asyncio.get_running_loop().time() - started
        finally:
            await client.close()
        assert response.status == 200
        assert elapsed >= 0.03

    async def test_a_streaming_representation_is_not_supported(self, source):
        # RAML describes one representation of one type; it has no notion of a
        # stream, so how many events one carries and how they are paced are
        # things no document states. An operation whose only response body is
        # `text/event-stream` is refused the same way structured XML is.
        client = await connected(source, MockOptions())
        try:
            events = await client.get('/events')
            lines = await client.get('/lines')
        finally:
            await client.close()
        assert events.status == 406
        assert lines.status == 406

    async def test_an_unencodable_state_value_fails_before_any_body_is_sent(self, source):
        resource = StatefulResource(
            name='objects',
            key_field='id',
            key_parameter='id',
            initial=({'id': 1, 'unsupported': {1}},),
            collection_get=('GET', '/state-objects'),
        )
        client = await connected(source, MockOptions(resources=(resource,)))
        try:
            response = await client.get('/state-objects')
            value = await response.json()
        finally:
            await client.close()
        assert response.status == 500
        assert value['error'] == 'mock generation failed'


class TestStatefulResources:
    async def test_crud_snapshot_and_reset(self, source):
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            initial=({'id': 1, 'name': 'Dune'},),
            collection_get=('GET', '/items'),
            item_get=('GET', '/items/{id}'),
            create=('POST', '/items/{id}'),
            update=('PUT', '/items/{id}'),
            delete=('DELETE', '/items/{id}'),
        )
        app = create_app(source, mock_options=MockOptions(resources=(resource,)))
        state = state_of(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            assert await (await client.get('/items')).json() == [{'id': 1, 'name': 'Dune'}]
            created = await client.post('/items/2', json={'id': 2, 'name': 'Foundation'})
            assert created.status == 201
            mismatch = await client.put('/items/2', json={'id': 3, 'name': 'Wrong key'})
            assert mismatch.status == 400
            updated = await client.put('/items/2', json={'id': 2, 'name': 'Foundation and Empire'})
            assert await updated.json() == {'id': 2, 'name': 'Foundation and Empire', 'category': 'book'}
            assert state.snapshot()['items'][1]['name'] == 'Foundation and Empire'
            assert (await client.delete('/items/2')).status == 204
            assert (await client.delete('/items/2')).status == 404
            missing = await client.get('/items/2')
            assert missing.status == 404
            assert await missing.json() == {'id': 0, 'name': 'missing'}
            state.reset('items')
            assert await (await client.get('/items')).json() == [{'id': 1, 'name': 'Dune'}]
        finally:
            await client.close()

    async def test_each_application_has_isolated_state(self, source):
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            initial=({'id': 1, 'name': 'Dune'},),
            collection_get=('GET', '/items'),
            create=('POST', '/items/{id}'),
        )
        first_app = create_app(source, mock_options=MockOptions(resources=(resource,)))
        second_app = create_app(source, mock_options=MockOptions(resources=(resource,)))
        first = TestClient(TestServer(first_app))
        second = TestClient(TestServer(second_app))
        await first.start_server()
        await second.start_server()
        try:
            await first.post('/items/2', json={'id': 2, 'name': 'Foundation'})
            assert len(state_of(first_app).snapshot()['items']) == 2
            assert len(state_of(second_app).snapshot()['items']) == 1
        finally:
            await first.close()
            await second.close()

    async def test_state_can_be_seeded_from_the_item_type_example(self, source):
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            seed_from_example=True,
            collection_get=('GET', '/items'),
            item_get=('GET', '/items/{id}'),
        )
        app = create_app(source, mock_options=MockOptions(resources=(resource,)))
        assert state_of(app).snapshot()['items'] == ({'id': 7, 'name': 'Dune'},)

    def test_resource_names_must_be_unique(self, source):
        resource = StatefulResource(name='items', key_field='id', key_parameter='id')
        with pytest.raises(ValueError, match='name is configured twice'):
            create_app(source, mock_options=MockOptions(resources=(resource, resource)))

    async def test_non_success_response_does_not_commit_create(self, source):
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            create=('POST', '/items/{id}'),
        )
        app = create_app(
            source,
            mock_options=MockOptions(
                routes={('POST', '/items/{id}'): RouteBehavior(status='422')},
                resources=(resource,),
            ),
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post('/items/2', json={'id': 2, 'name': 'Foundation'})
        finally:
            await client.close()
        assert response.status == 422
        assert state_of(app).snapshot()['items'] == ()

    async def test_a_declared_state_error_status_is_answered_from_the_raml(self, source):
        # `/items/{id}` declares `499:` itself. RAML has no `4xx` response class
        # -- the parser rejects the key (docs/08 § 6.1) -- so a mock status
        # outside the usual few is reachable only if the document names it.
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            item_get=('GET', '/items/{id}'),
            item_missing_status=499,
        )
        client = await connected(source, MockOptions(resources=(resource,)))
        try:
            response = await client.get('/items/2')
        finally:
            await client.close()
        assert response.status == 499

    async def test_an_undeclared_state_error_status_still_answers(self, source):
        # A failure the document does not declare falls back to the generic
        # problem body rather than failing the request, because an error
        # response needs no declared representation to be meaningful.
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            item_get=('GET', '/items/{id}'),
            item_missing_status=451,
        )
        client = await connected(source, MockOptions(resources=(resource,)))
        try:
            response = await client.get('/items/2')
            body = await response.json()
        finally:
            await client.close()
        assert response.status == 451
        assert body['error'] == 'stateful resource operation failed'

    @pytest.mark.parametrize('status', [399, 600])
    def test_state_failure_statuses_are_client_or_server_errors(self, status):
        with pytest.raises(ValueError, match='between 400 and 599'):
            StatefulResource(name='items', key_field='id', key_parameter='id', conflict_status=status)

    @pytest.mark.parametrize('status', [200, 204])
    def test_only_delete_may_succeed_on_a_missing_item(self, status):
        # DELETE is idempotent and its answer carries no representation, so a
        # missing item may still be a success. Every other missing-item case
        # would have to synthesize a body for a thing that is not there.
        StatefulResource(name='items', key_field='id', key_parameter='id', delete_missing_status=status)
        with pytest.raises(ValueError, match='between 400 and 599'):
            StatefulResource(name='items', key_field='id', key_parameter='id', item_missing_status=status)

    @pytest.mark.parametrize('status', [199, 302, 600])
    def test_a_delete_missing_status_is_a_success_or_a_failure(self, status):
        with pytest.raises(ValueError, match='2xx status or between 400 and 599'):
            StatefulResource(name='items', key_field='id', key_parameter='id', delete_missing_status=status)

    async def test_a_repeated_delete_succeeds_when_the_document_declares_it(self, source):
        # `DELETE /items/{id}` declares `204:`, so the mock can answer a delete
        # of something already gone the way the API says it does.
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            create=('POST', '/items/{id}'),
            delete=('DELETE', '/items/{id}'),
            delete_missing_status=204,
        )
        client = await connected(source, MockOptions(resources=(resource,)))
        try:
            await client.post('/items/3', json={'id': 3, 'name': 'Dune'})
            first = await client.delete('/items/3')
            repeated = await client.delete('/items/3')
        finally:
            await client.close()
        assert first.status == 204
        assert repeated.status == 204

    def test_a_delete_cannot_succeed_with_a_status_the_document_omits(self, source):
        # 200 is not among the delete's declared responses, and unlike a failure
        # there is no generic body to fall back on -- so it is refused at build
        # time rather than answered with a status the API never promised.
        resource = StatefulResource(
            name='items',
            key_field='id',
            key_parameter='id',
            delete=('DELETE', '/items/{id}'),
            delete_missing_status=200,
        )
        with pytest.raises(ValueError, match='delete_missing_status is not declared'):
            create_app(source, mock_options=MockOptions(resources=(resource,)))


class TestAuthentication:
    async def test_basic_oauth_scopes_null_and_custom_alternatives(self, source):
        credentials = base64.b64encode(b'admin:secret').decode()
        authentication = Authentication(
            basic={'basic': (BasicCredentials('admin', 'secret'),)},
            bearer={
                'oauth': (
                    BearerToken('reader', frozenset({'read'})),
                    BearerToken('writer', frozenset({'write'})),
                )
            },
        )
        secured = await connected(source, MockOptions(authentication=authentication))
        body = {'id': 2, 'name': 'Foundation'}
        try:
            assert (await secured.post('/secure-write', json=body)).status == 401
            assert (await secured.get('/open')).status == 200
            assert (
                await secured.post('/secure-write', json=body, headers={'Authorization': 'Bearer reader'})
            ).status == 403
            assert (
                await secured.post('/secure-write', json=body, headers={'Authorization': 'Bearer writer'})
            ).status == 200
            assert (
                await secured.post('/secure-write', json=body, headers={'Authorization': f'Basic {credentials}'})
            ).status == 200
        finally:
            await secured.close()

        async def api_key(request, _route):
            return AuthDecision(allowed=request.headers.get('X-API-Key') == 'secret')

        custom = await connected(
            source,
            MockOptions(authentication=Authentication(custom={'apiKey': api_key})),
        )
        try:
            assert (await custom.get('/protected')).status == 401
            assert (await custom.get('/protected', headers={'X-API-Key': 'secret'})).status == 200
        finally:
            await custom.close()

    async def test_custom_forbidden_headers_survive_other_failed_alternatives(self, source):
        async def forbidden(_request, _route):
            return AuthDecision(allowed=False, status=403, headers={'X-Auth-Reason': 'blocked'})

        authentication = Authentication(
            bearer={'oauth': (BearerToken('reader', frozenset({'read'})),)},
            custom={'apiKey': forbidden},
        )
        client = await connected(source, MockOptions(authentication=authentication))
        try:
            response = await client.post(
                '/secure-write',
                json={'id': 2, 'name': 'Foundation'},
                headers={'Authorization': 'Bearer reader'},
            )
        finally:
            await client.close()
        assert response.status == 403
        assert response.headers['X-Auth-Reason'] == 'blocked'

    @pytest.mark.parametrize('status', [399, 600])
    def test_denied_authentication_status_is_validated(self, status):
        with pytest.raises(ValueError, match='between 400 and 599'):
            AuthDecision(allowed=False, status=status)
