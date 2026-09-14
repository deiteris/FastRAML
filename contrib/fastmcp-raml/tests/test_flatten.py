"""Flat arguments and the map back, held to what fastmcp produces.

`flatten` replaces `_combine_schemas_and_map_params`, which is private. The
import of it lives here rather than in the package: a break then shows up as a
failing test naming the difference, not as a server whose tools take the wrong
arguments.

The comparison is the gate. The cases below it are the ones a reader needs
spelled out -- a collision, a body that is not an object -- and would be
unreadable as a diff of two dicts.
"""

from __future__ import annotations

import pytest
from conftest import TENANT
from fastmcp.utilities.openapi import HTTPRoute, ParameterInfo, RequestBodyInfo
from fastmcp.utilities.openapi.schemas import _combine_schemas_and_map_params
from fastraml import ParseOptions, parse_from_path

from fastmcp_raml import RAMLProvider, to_http_routes
from fastmcp_raml.flatten import flatten


def route(**overrides) -> HTTPRoute:
    return HTTPRoute(path='/x', method='GET', operation_id='x', **overrides)


def parameter(name: str, location: str, **overrides) -> ParameterInfo:
    return ParameterInfo(name=name, location=location, schema={'type': 'string'}, **overrides)


class TestItAgreesWithFastmcp:
    """The same input through both, for every route the sample yields."""

    def test_every_route_in_the_sample(self, sample):
        for built in to_http_routes(sample).routes:
            # Built fresh: upstream writes a body description back into the
            # route it was given, so running it second would see ours.
            theirs, their_map = _combine_schemas_and_map_params(built, convert_refs=False)
            ours, our_map = flatten(built)
            where = f'{built.method} {built.path}'
            assert ours == theirs, where
            assert our_map == their_map, where

    @pytest.mark.parametrize(
        'built',
        [
            pytest.param(route(), id='nothing'),
            pytest.param(route(parameters=[parameter('q', 'query')]), id='one-query'),
            pytest.param(
                route(parameters=[parameter('id', 'path', required=True), parameter('id', 'query')]),
                id='same-name-two-places',
            ),
            pytest.param(
                route(
                    parameters=[parameter('id', 'path', required=True, description='which one')],
                    request_body=RequestBodyInfo(
                        required=True,
                        content_schema={
                            'application/json': {
                                'type': 'object',
                                'properties': {'id': {'type': 'integer'}, 'note': {'type': 'string'}},
                                'required': ['id'],
                            }
                        },
                    ),
                ),
                id='path-collides-with-body',
            ),
            pytest.param(
                route(
                    request_body=RequestBodyInfo(
                        required=True,
                        content_schema={'application/json': {'type': 'array', 'items': {'type': 'string'}}},
                    )
                ),
                id='array-body',
            ),
            pytest.param(
                route(
                    request_body=RequestBodyInfo(
                        required=True,
                        content_schema={
                            'application/json': {'title': 'Anything postable', 'anyOf': [{'type': 'string'}]}
                        },
                    )
                ),
                id='titled-non-object-body',
            ),
            pytest.param(
                route(
                    request_body=RequestBodyInfo(
                        required=True,
                        description='what to send',
                        content_schema={'application/json': {'$ref': '#/$defs/Book'}},
                    ),
                    request_schemas={'Book': {'type': 'object'}},
                ),
                id='ref-body',
            ),
            pytest.param(
                route(parameters=[parameter('t', 'header'), parameter('s', 'cookie')]),
                id='header-and-cookie',
            ),
        ],
    )
    def test_each_shape_of_route(self, built):
        theirs, their_map = _combine_schemas_and_map_params(built, convert_refs=False)
        ours, our_map = flatten(built)
        assert ours == theirs
        assert our_map == their_map


class TestTheCasesWorthNaming:
    def test_a_collision_is_suffixed_and_labelled(self):
        built = route(
            parameters=[parameter('id', 'path', required=True)],
            request_body=RequestBodyInfo(
                required=True,
                content_schema={'application/json': {'type': 'object', 'properties': {'id': {'type': 'integer'}}}},
            ),
        )
        schema, mapping = flatten(built)
        # The body keeps the bare name; the parameter is the one that moves.
        assert sorted(schema['properties']) == ['id', 'id__path']
        assert schema['properties']['id__path']['description'] == '(Path parameter)'
        assert mapping['id__path'] == {'location': 'path', 'openapi_name': 'id'}
        assert mapping['id'] == {'location': 'body', 'openapi_name': 'id'}

    def test_a_body_that_is_not_an_object_becomes_one_argument(self):
        built = route(
            request_body=RequestBodyInfo(
                required=True, content_schema={'application/json': {'type': 'array', 'items': {'type': 'string'}}}
            )
        )
        schema, mapping = flatten(built)
        assert schema['properties']['body']['type'] == 'array'
        assert schema['required'] == ['body']
        assert mapping['body']['location'] == 'body'

    def test_the_route_is_not_mutated(self):
        built = route(
            request_body=RequestBodyInfo(
                required=True,
                description='what to send',
                content_schema={'application/json': {'type': 'object', 'properties': {'a': {'type': 'string'}}}},
            )
        )
        flatten(built)
        # Upstream writes the description into the route's own dict. This does
        # not, so flattening twice cannot give two different answers.
        assert 'description' not in built.request_body.content_schema['application/json']

    def test_definitions_are_carried_through(self):
        built = route(
            request_body=RequestBodyInfo(
                required=True,
                content_schema={
                    'application/json': {
                        'type': 'object',
                        'properties': {'next': {'$ref': '#/$defs/Chain'}},
                    }
                },
            ),
            request_schemas={'Chain': {'type': 'object'}},
        )
        schema, _ = flatten(built)
        assert schema['$defs'] == {'Chain': {'type': 'object'}}


class TestTheDirectorAcceptsWhatWeBuild:
    """The map is only right if the director can read it."""

    def test_a_flat_call_reaches_the_right_places(self, sample):
        provider = RAMLProvider(sample, base_uri_parameters=TENANT)
        built = next(r for r in to_http_routes(sample).routes if (r.method, r.path) == ('GET', '/deliveries'))
        request = provider._director.build(
            built, {'since': '2024-06-01T00:00:00Z', 'city': 'Bristol'}, 'https://api.example'
        )
        assert request.url.params['since'] == '2024-06-01T00:00:00Z'
        assert request.url.params['city'] == 'Bristol'

    def test_a_body_is_rebuilt_from_its_properties(self, sample_path, sample_options):
        raml = parse_from_path(
            sample_path, ParseOptions(unwrap=True, validate=True, workspace_root=sample_options.workspace_root)
        )
        provider = RAMLProvider(raml, base_uri_parameters=TENANT)
        built = next(r for r in to_http_routes(raml).routes if (r.method, r.path) == ('POST', '/books'))
        request = provider._director.build(built, {'id': 'b-1', 'title': 'Dune'}, 'https://api.example')
        assert b'"title":"Dune"' in request.content
