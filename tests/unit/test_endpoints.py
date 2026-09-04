"""Stage 2, media types, URI parameters and propagation.

docs/08-templates-and-endpoints.md section 8. `test_source_ir.py` covers the
stage-1 split; this covers what stage 2 makes of what survives it, and the
P4/P6 driver around it.
"""

from __future__ import annotations

import pytest

from pyraml import ParseOptions, RamlError, parse_from_path
from pyraml.domains import DomainLocation

API = '#%RAML 1.0\ntitle: T\n'
JSON = API + 'mediaType: application/json\n'


def parse(workspace, body: str, head: str = API, **options):
    root = workspace({'api.raml': head + body})
    return parse_from_path(root / 'api.raml', ParseOptions(**options) if options else None)


def fails(workspace, body: str, head: str = API) -> RamlError | None:
    try:
        parse(workspace, body, head)
    except RamlError as err:
        return err
    return None


def messages(error: RamlError) -> set[str]:
    return {trace.message for chain in error.chains() for trace in chain}


class TestStructure:
    def test_endpoints_are_indexed_by_full_uri(self, workspace):
        raml = parse(workspace, '/users:\n  /{id}:\n    /photos:\n')
        assert set(raml.endpoints) == {'/users', '/users/{id}', '/users/{id}/photos'}

    def test_a_nested_endpoint_is_reachable_from_its_parent(self, workspace):
        raml = parse(workspace, '/users:\n  /{id}:\n')
        assert list(raml.endpoints['/users'].endpoints) == ['/{id}']

    def test_declaration_order_is_preserved(self, workspace):
        raml = parse(workspace, '/z:\n  post:\n  get:\n/a:\n')
        assert list(raml.endpoints) == ['/z', '/a']
        assert list(raml.endpoints['/z'].operations) == ['post', 'get']

    def test_a_duplicate_absolute_uri_is_rejected(self, workspace):
        error = fails(workspace, '/users:\n  /foo:\n/users/foo:\n')
        assert error is not None
        assert 'duplicate resource URI' in messages(error)

    def test_templates_with_different_names_coexist(self, workspace):
        # Comparison is on the template text, unexpanded (docs/08 § 8.1).
        raml = parse(workspace, '/users/{userId}:\n/users/{username}:\n/users/me:\n')
        assert len(raml.endpoints) == 3


class TestOperations:
    def test_common_facets_land_on_the_operation(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    displayName: List\n    description: d\n')
        get = raml.endpoints['/users'].operations['get']
        assert get.display_name.value == 'List'
        assert get.description.value == 'd'

    def test_protocols_are_upper_cased(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    protocols: [http, https]\n')
        assert raml.endpoints['/users'].operations['get'].protocols == ['HTTP', 'HTTPS']

    def test_headers_and_query_parameters_are_properties(self, workspace):
        raml = parse(
            workspace,
            '/users:\n  get:\n    headers:\n      X-Key: string\n    queryParameters:\n      q?: string\n',
        )
        request = raml.endpoints['/users'].operations['get'].request
        assert request.headers['X-Key'].required
        assert not request.query_parameters['q'].required

    def test_a_query_string_is_a_whole_shape(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    queryString:\n      properties:\n        q: string\n')
        request = raml.endpoints['/users'].operations['get'].request
        assert request.query_string.type == 'object'

    def test_query_string_and_query_parameters_are_mutually_exclusive(self, workspace):
        error = fails(workspace, '/users:\n  get:\n    queryString: string\n    queryParameters:\n      q: string\n')
        assert error is not None
        assert 'queryString and queryParameters are mutually exclusive' in messages(error)

    def test_an_unknown_method_field_is_rejected(self, workspace):
        error = fails(workspace, '/users:\n  get:\n    nonsense: 1\n')
        assert error is not None
        assert 'unknown field' in messages(error)


class TestResponses:
    def test_responses_are_keyed_by_the_code_as_written(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    responses:\n      200:\n      "404":\n')
        assert list(raml.endpoints['/users'].operations['get'].responses) == ['200', '404']

    def test_a_response_carries_headers_and_a_description(self, workspace):
        raml = parse(
            workspace,
            '/users:\n  get:\n    responses:\n      200:\n        description: ok\n'
            '        headers:\n          X-Rate: integer\n',
        )
        response = raml.endpoints['/users'].operations['get'].responses['200']
        assert response.description.value == 'ok'
        assert response.headers['X-Rate'].base.type == 'integer'

    def test_an_unknown_response_field_is_rejected(self, workspace):
        error = fails(workspace, '/users:\n  get:\n    responses:\n      200:\n        nonsense: 1\n')
        assert error is not None
        assert 'unknown field' in messages(error)


class TestBodies:
    def test_media_type_keys_are_taken_as_written(self, workspace):
        raml = parse(
            workspace,
            '/users:\n  post:\n    body:\n      application/json: string\n      application/xml: string\n',
        )
        bodies = raml.endpoints['/users'].operations['post'].request.bodies
        assert list(bodies) == ['application/json', 'application/xml']
        assert bodies['application/json'].shape.type == 'string'

    def test_a_bodyless_declaration_uses_the_default_media_types(self, workspace):
        raml = parse(workspace, '/users:\n  post:\n    body:\n      type: string\n', head=JSON)
        bodies = raml.endpoints['/users'].operations['post'].request.bodies
        assert list(bodies) == ['application/json']

    def test_it_is_instantiated_once_per_default(self, workspace):
        head = API + 'mediaType: [application/json, application/xml]\n'
        raml = parse(workspace, '/users:\n  post:\n    body:\n      type: string\n', head=head)
        bodies = raml.endpoints['/users'].operations['post'].request.bodies
        assert list(bodies) == ['application/json', 'application/xml']
        # Separate shapes: sharing one would alias their facets through P7.
        assert bodies['application/json'].shape is not bodies['application/xml'].shape

    def test_no_media_type_anywhere_is_an_error(self, workspace):
        error = fails(workspace, '/users:\n  post:\n    body:\n      type: string\n')
        assert error is not None
        assert 'explicit media type is required' in messages(error)

    def test_mixing_media_types_with_facets_is_an_error(self, workspace):
        # `body: {application/json: ..., type: Foo}` — the common mistake.
        error = fails(
            workspace, '/users:\n  post:\n    body:\n      application/json: string\n      type: string\n', head=JSON
        )
        assert error is not None
        assert 'body mixes media types with facets' in messages(error)

    def test_a_body_with_no_type_is_any(self, workspace):
        raml = parse(workspace, '/users:\n  post:\n    body:\n      application/json:\n')
        assert raml.endpoints['/users'].operations['post'].request.bodies['application/json'].shape.type == 'any'

    def test_a_response_body_is_decoded_too(self, workspace):
        raml = parse(
            workspace,
            '/users:\n  get:\n    responses:\n      200:\n        body:\n          application/json: string\n',
        )
        response = raml.endpoints['/users'].operations['get'].responses['200']
        assert response.bodies['application/json'].shape.type == 'string'


class TestUriParameters:
    def test_an_undeclared_variable_is_synthesised_as_a_required_string(self, workspace):
        raml = parse(workspace, '/users/{id}:\n')
        prop = raml.endpoints['/users/{id}'].uri_parameters['id']
        assert prop.required
        assert prop.base.type == 'string'

    def test_a_declared_parameter_wins(self, workspace):
        raml = parse(workspace, '/users/{id}:\n  uriParameters:\n    id: integer\n')
        assert raml.endpoints['/users/{id}'].uri_parameters['id'].base.type == 'integer'

    def test_a_parameter_absent_from_the_template_is_an_error(self, workspace):
        error = fails(workspace, '/users:\n  uriParameters:\n    id: string\n')
        assert error is not None
        assert 'uri parameter is not used' in messages(error)

    @pytest.mark.parametrize(
        'facet',
        [
            'default: a/b',
            'example: a/b',
            'enum: [ok, a/b]',
        ],
        ids=['default', 'example', 'enum'],
    )
    def test_a_constraint_value_may_not_contain_a_slash(self, workspace, facet):
        # Spec § Template URIs: a matched value must not contain a slash, so a
        # constraint naming one describes something unmatchable.
        error = fails(workspace, f'/users/{{id}}:\n  uriParameters:\n    id:\n      type: string\n      {facet}\n')
        assert error is not None
        assert 'uri parameter value must not contain a slash' in messages(error)

    def test_a_slash_free_constraint_is_fine(self, workspace):
        assert (
            fails(workspace, '/users/{id}:\n  uriParameters:\n    id:\n      type: string\n      default: ok\n') is None
        )

    def test_a_malformed_template_reports_its_position(self, workspace):
        error = fails(workspace, '/users/{id:\n')
        assert error is not None


class TestPropagation:
    """P6 — ancestors first, then the endpoint's own, in path order."""

    def test_a_nested_resource_exposes_the_whole_path(self, workspace):
        raml = parse(workspace, '/users/{userId}:\n  /photos/{photoId}:\n')
        assert list(raml.endpoints['/users/{userId}/photos/{photoId}'].uri_parameters) == ['userId', 'photoId']

    def test_the_parent_keeps_only_its_own(self, workspace):
        raml = parse(workspace, '/users/{userId}:\n  /photos/{photoId}:\n')
        assert list(raml.endpoints['/users/{userId}'].uri_parameters) == ['userId']

    def test_three_levels_stay_in_path_order(self, workspace):
        raml = parse(workspace, '/a/{one}:\n  /b/{two}:\n    /c/{three}:\n')
        deepest = raml.endpoints['/a/{one}/b/{two}/c/{three}']
        assert list(deepest.uri_parameters) == ['one', 'two', 'three']

    def test_a_child_may_not_declare_an_ancestor_s_parameter(self, workspace):
        # `uriParameters` describes *this* resource's template. `/photos` has no
        # `{id}` of its own, so declaring one is the "not used" error even
        # though the inherited parameter is visible on the child. Measured
        # against go-raml, which rejects it with the same message.
        error = fails(workspace, '/users/{id}:\n  /photos:\n    uriParameters:\n      id: integer\n')
        assert error is not None
        assert 'uri parameter is not used' in messages(error)

    def test_the_inherited_parameter_is_still_exposed(self, workspace):
        raml = parse(workspace, '/users/{id}:\n  /photos:\n')
        assert list(raml.endpoints['/users/{id}/photos'].uri_parameters) == ['id']


class TestRegisteredForLaterPasses:
    """Every shape stage 2 creates must reach P9 and P10.

    `unwrap_shapes` and `validate_shapes` iterate `fragment_typedefs` and
    nothing else, so a shape that skips `put_typedef` is silently never
    flattened and never validated.
    """

    def test_a_body_shape_is_registered(self, workspace):
        raml = parse(workspace, '/users:\n  post:\n    body:\n      application/json: string\n')
        shape = raml.endpoints['/users'].operations['post'].request.bodies['application/json'].shape
        assert shape in raml.typedefs_in(raml.location)

    def test_a_header_shape_is_registered(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    headers:\n      X-Key: string\n')
        prop = raml.endpoints['/users'].operations['get'].request.headers['X-Key']
        assert prop.base in raml.typedefs_in(raml.location)

    def test_a_synthesised_uri_parameter_is_registered(self, workspace):
        raml = parse(workspace, '/users/{id}:\n')
        prop = raml.endpoints['/users/{id}'].uri_parameters['id']
        assert prop.base in raml.typedefs_in(raml.location)

    def test_a_query_string_shape_is_registered(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    queryString: string\n')
        shape = raml.endpoints['/users'].operations['get'].request.query_string
        assert shape in raml.typedefs_in(raml.location)

    def test_an_endpoint_body_is_validated(self, workspace):
        # The end of the chain: P10 sees a body's example because stage 2
        # registered the shape.
        error = fails(
            workspace,
            '/users:\n  post:\n    body:\n      application/json:\n        type: integer\n        example: notanumber\n',
        )
        assert error is None, 'validation is off without the option'
        root = workspace(
            {
                'api.raml': API + '/users:\n  post:\n    body:\n      application/json:\n'
                '        type: integer\n        example: notanumber\n'
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
        assert 'invalid example' in {t.message for c in caught.value.chains() for t in c}


class TestAnnotationTargets:
    """The five sites this phase creates (docs/09 § B5).

    A missing `target_scope` is silent — the annotation records the enclosing
    site — so each one needs a test that names it.
    """

    DECLARE = 'annotationTypes:\n  ann: any\n'

    @pytest.mark.parametrize(
        ('body', 'expected'),
        [
            ('/users:\n  (ann): 1\n', DomainLocation.RESOURCE),
            ('/users:\n  get:\n    (ann): 1\n', DomainLocation.METHOD),
            ('/users:\n  get:\n    responses:\n      200:\n        (ann): 1\n', DomainLocation.RESPONSE),
            (
                '/users:\n  post:\n    body:\n      application/json:\n        (ann): 1\n',
                DomainLocation.REQUEST_BODY,
            ),
            (
                '/users:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n            (ann): 1\n',
                DomainLocation.RESPONSE_BODY,
            ),
        ],
        ids=['resource', 'method', 'response', 'request-body', 'response-body'],
    )
    def test_each_site_records_itself(self, workspace, body, expected):
        raml = parse(workspace, self.DECLARE + body)
        assert raml.domain_extensions[0].target is expected

    def test_a_bodyless_body_is_a_type_declaration_not_a_body(self, workspace):
        # The spec's table calls RequestBody/ResponseBody "the body node", which
        # in the media-type spelling is the node above. A `body:` written
        # without media-type keys *is* the type declaration.
        raml = parse(workspace, self.DECLARE + '/users:\n  post:\n    body:\n      (ann): 1\n', head=JSON)
        assert raml.domain_extensions[0].target is DomainLocation.TYPE_DECLARATION

    def test_a_uri_parameter_is_a_type_declaration(self, workspace):
        raml = parse(workspace, self.DECLARE + '/users/{id}:\n  uriParameters:\n    id:\n      (ann): 1\n')
        assert raml.domain_extensions[0].target is DomainLocation.TYPE_DECLARATION


class TestNonApiFragments:
    def test_a_library_has_no_endpoints(self, workspace):
        root = workspace({'lib.raml': '#%RAML 1.0 Library\ntypes:\n  T: string\n'})
        assert parse_from_path(root / 'lib.raml').endpoints == {}
