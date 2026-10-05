"""Stage 2, media types, URI parameters and propagation.

docs/08-templates-and-endpoints.md § 6. `test_source_ir.py` covers the
stage-1 split; this covers what stage 2 makes of what survives it, and the
P4/P6 driver around it.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError
from fastraml.domains import DomainLocation
from tests.diagnostics import messages, traces

API = '#%RAML 1.0\ntitle: T\n'
JSON = API + 'mediaType: application/json\n'


def parse(workspace, body: str, head: str = API, **options):
    return workspace.document(head + body, ParseOptions(**options) if options else None)


def fails(workspace, body: str, head: str = API) -> RamlError | None:
    return workspace.rejection(head + body)


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
        # Comparison is on the template text, unexpanded (docs/08 § 6.1).
        raml = parse(workspace, '/users/{userId}:\n/users/{username}:\n/users/me:\n')
        assert len(raml.endpoints) == 3


#: `protocols: <value>` written at the API root and on a method: one decoder
#: reads both (docs/08 § 6.1).
LEVELS = {
    'root': lambda value: f'protocols: {value}\n',
    'method': lambda value: f'/users:\n  get:\n    protocols: {value}\n',
}


def frames(error: RamlError) -> list[tuple[str, dict]]:
    return [(frame.message, frame.info) for frame in traces(error)]


class TestProtocols:
    def test_root_protocols_are_stored_upper_cased(self, workspace):
        raml = parse(workspace, LEVELS['root']('[http, hTtPs]'))
        assert [facet.value for facet in raml.entry_point.protocols] == ['HTTP', 'HTTPS']
        assert raml.global_protocols == ['HTTP', 'HTTPS']

    def test_method_protocols_are_stored_upper_cased(self, workspace):
        raml = parse(workspace, LEVELS['method']('[http, https]'))
        assert [facet.value for facet in raml.endpoints['/users'].operations['get'].protocols] == ['HTTP', 'HTTPS']

    @pytest.mark.parametrize('level', LEVELS)
    def test_an_empty_list_is_rejected_at_either_level(self, workspace, level):
        """Spec § Protocols: "a non-empty array"; a method accepted `[]` while
        its decoder was its own."""
        error = fails(workspace, LEVELS[level]('[]'))
        assert error is not None
        assert frames(error) == [('protocols must not be empty', {})]

    @pytest.mark.parametrize('level', LEVELS)
    @pytest.mark.parametrize('value', ['HTTPS', '{a: b}'])
    def test_a_non_sequence_is_rejected_with_one_key_at_either_level(self, workspace, level, value):
        error = fails(workspace, LEVELS[level](value))
        assert error is not None
        assert frames(error) == [('protocols must be an array', {})]

    @pytest.mark.parametrize('level', LEVELS)
    def test_an_unknown_protocol_is_rejected_at_either_level(self, workspace, level):
        error = fails(workspace, LEVELS[level]('[HTTP, ftp]'))
        assert error is not None
        assert frames(error) == [('unknown protocol', {'protocol': 'ftp'})]

    @pytest.mark.parametrize(
        ('document', 'effective'),
        [
            ("baseUri: 'https://x/'\n", ['HTTPS']),
            ("baseUri: 'HTTP://x/'\n", ['HTTP']),
            ("baseUri: 'https://x/'\nprotocols: [HTTP]\n", ['HTTP']),
            ("baseUri: '{scheme}://x/'\n", []),
            ("baseUri: 'ftp://x/'\n", []),
            ("baseUri: 'x.test/api'\n", []),
            ('', []),
        ],
    )
    def test_without_protocols_the_base_uri_scheme_is_effective(self, workspace, document, effective):
        """Spec § Protocols: without a `protocols` node the baseUri's protocol
        is used. A templated scheme leaves it undetermined; the authored
        `protocols` is not filled in (docs/08 § 6.1)."""
        raml = parse(workspace, document)
        assert raml.global_protocols == effective
        assert [facet.value for facet in raml.entry_point.protocols] == (['HTTP'] if 'protocols' in document else [])

    def test_a_method_accepts_the_annotated_scalar_item_the_root_does(self, workspace):
        head = API + 'annotationTypes:\n  a:\n'
        raml = parse(workspace, LEVELS['method']('[{value: https, (a): 1}]'), head)
        [facet] = raml.endpoints['/users'].operations['get'].protocols
        assert facet.value == 'HTTPS'
        # Kept on the model, as the root keeps its own, and bound like any other.
        assert facet.annotations['a'].value.raw == 1
        assert facet.annotations['a'].target is DomainLocation.METHOD
        error = fails(workspace, LEVELS['method']('[{value: https, (b): 1}]'), head)
        assert error is not None
        assert 'reference not found' in {message for message, _ in frames(error)}


class TestOperations:
    def test_common_facets_land_on_the_operation(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    displayName: List\n    description: d\n')
        get = raml.endpoints['/users'].operations['get']
        assert get.display_name.value == 'List'
        assert get.description.value == 'd'

    @pytest.mark.parametrize('method', ['trace', 'connect'])
    def test_only_the_seven_raml_methods_are_methods(self, workspace, method):
        """Spec § Methods (raml-10.md L1944) lists get, patch, put, post, delete,
        head and options; any other key on a resource is unknown.
        """
        error = fails(workspace, f'/users:\n  {method}:\n  get:\n')
        assert error is not None
        assert [frame.info for frame in traces(error) if frame.message == 'unknown field'] == [{'field': method}]

    @pytest.mark.parametrize('method', ['trace', 'connect'])
    def test_a_resource_type_cannot_contribute_a_non_raml_method(self, workspace, method):
        error = fails(workspace, f'resourceTypes:\n  r:\n    {method}:\n/users:\n  type: r\n')
        assert error is not None
        assert [
            frame.info for frame in traces(error) if frame.message == 'resource type method must be an HTTP method'
        ] == [{'key': method}]

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

    @pytest.mark.parametrize(
        'document',
        [
            '/users:\n  get:\n    queryString:\n      type: array\n      items: string\n',
            '/users:\n  get:\n    queryString: string[]\n',
            '/users:\n  get:\n    queryString:\n      type: object | string[]\n',
            'types:\n  Q: string[]\n/users:\n  get:\n    queryString: Q\n',
            'types:\n  Q: string[]\n  R: Q\n/users:\n  get:\n    queryString:\n      type: R\n',
            'types:\n  Q: string | integer[]\n/users:\n  get:\n    queryString:\n      type: object | Q\n',
            (
                'types:\n  Q: string | integer[]\n  R:\n    type: Q\n    description: d\n'
                '/users:\n  get:\n    queryString:\n      type: object | R\n'
            ),
            'traits:\n  t:\n    queryString: string[]\n/users:\n  get:\n    is: [t]\n',
            'securitySchemes:\n  s:\n    type: x-custom\n    describedBy:\n      queryString: string[]\n',
        ],
    )
    def test_a_query_string_that_admits_an_array_is_rejected(self, workspace, document):
        # Spec § The Query String as a Whole: after expanding every union, each
        # base type MUST be a scalar type or the object type. Checked in P10,
        # on the flattened form, so only when validating.
        assert parse(workspace, document) is not None
        with pytest.raises(RamlError) as caught:
            parse(workspace, document, validate=True)
        assert 'query string must be a scalar or object type' in messages(caught.value)

    @pytest.mark.parametrize(
        'query_string',
        ['string', 'integer | boolean', '\n      properties:\n        q: string[]', 'object | string', 'nil'],
    )
    def test_a_scalar_or_object_query_string_is_accepted(self, workspace, query_string):
        # An array-typed *property* is what repeats a parameter; only the
        # query string's own type is restricted.
        assert parse(workspace, f'/users:\n  get:\n    queryString: {query_string}\n', validate=True) is not None

    def test_an_unknown_method_field_is_rejected(self, workspace):
        error = fails(workspace, '/users:\n  get:\n    nonsense: 1\n')
        assert error is not None
        assert 'unknown field' in messages(error)


class TestResponses:
    def test_numeric_and_quoted_response_codes_are_normalised_to_strings(self, workspace):
        raml = parse(workspace, '/users:\n  get:\n    responses:\n      200:\n      "404":\n')
        responses = raml.endpoints['/users'].operations['get'].responses
        assert list(responses) == ['200', '404']
        assert responses['200'].code == '200'

    def test_numeric_and_quoted_forms_are_duplicate_response_keys(self, workspace):
        error = fails(workspace, '/users:\n  get:\n    responses:\n      200:\n      "200":\n')
        assert error is not None
        assert error.head.message == 'duplicate key'
        assert error.head.info == {'key': '200'}

    @pytest.mark.parametrize('code', ['2xx', 'default', '099', '600'])
    def test_response_code_must_be_a_concrete_100_to_599_status(self, workspace, code):
        error = fails(workspace, f'/users:\n  get:\n    responses:\n      {code}:\n')
        assert error is not None
        assert 'status code must be a 3-digit number' in messages(error)

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
        # And each records that its media type was not written, so a reader
        # tells them from bodies written under their own keys (docs/08 § 6.3).
        assert [body.media_type_written for body in bodies.values()] == [False, False]

    def test_a_body_under_its_media_type_records_that_it_was_written(self, workspace):
        raml = parse(workspace, '/users:\n  post:\n    body:\n      application/json: string\n')
        (body,) = raml.endpoints['/users'].operations['post'].request.bodies.values()
        assert body.media_type_written

    @pytest.mark.parametrize('site', ['request', 'response'])
    def test_a_bodyless_declaration_is_placed_at_its_body_key(self, workspace, site):
        # It was placed nowhere: a lint finding on it fell back to the file's start.
        text = '/users:\n  post:\n    ' + (
            'body:\n' if site == 'request' else 'responses:\n      200:\n        body:\n'
        )
        raml = parse(workspace, text + '          type: string\n', head=JSON)
        operation = raml.endpoints['/users'].operations['post']
        (body,) = (operation.request.bodies if site == 'request' else operation.responses['200'].bodies).values()
        line = (JSON + text).count('\n')
        assert (body.key_pos.line, body.key_pos.end_column - body.key_pos.column) == (line, len('body'))

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


#: `body: {<key>: string}` at each site a body is written, reaching the model
#: after templates are applied (docs/08 § 6.3).
BODY_SITES = {
    'request': lambda key: f'/a:\n  post:\n    body:\n      {key}: string\n',
    'response': lambda key: f'/a:\n  get:\n    responses:\n      200:\n        body:\n          {key}: string\n',
    'trait': lambda key: f'traits:\n  t:\n    body:\n      {key}: string\n/a:\n  post:\n    is: [t]\n',
    'resource type': lambda key: (
        f'resourceTypes:\n  r:\n    post:\n      body:\n        {key}: string\n/a:\n  type: r\n'
    ),
    'describedBy': lambda key: (
        'securitySchemes:\n  s:\n    type: x-custom\n    describedBy:\n      responses:\n        401:\n'
        f'          body:\n            {key}: string\n/a:\n  get:\n    securedBy: [s]\n'
    ),
}


class TestBodyMediaTypeKeys:
    """Spec section Bodies: each key "MUST be a media type string conforming to
    the media type specification in RFC6838". A media range is kept (docs/08 § 6.3).
    """

    @pytest.mark.parametrize('site', sorted(BODY_SITES))
    def test_a_key_that_is_not_a_media_type_is_refused_at_every_site(self, workspace, site):
        error = fails(workspace, BODY_SITES[site]('application/json/x'))
        assert error is not None
        assert ('invalid media type', {'media type': 'application/json/x'}) in frames(error)

    @pytest.mark.parametrize('site', sorted(BODY_SITES))
    def test_a_media_type_with_parameters_is_accepted_at_every_site(self, workspace, site):
        assert fails(workspace, BODY_SITES[site]("'application/json; charset=utf-8'")) is None

    @pytest.mark.parametrize('key', ["'*/*'", 'application/*'])
    def test_a_media_range_is_accepted(self, workspace, key):
        # The JSON Schema check (docs/10 § 7) and `restricted-request-media-type`
        # both read a wildcard key as a body that accepts every format.
        raml = parse(workspace, BODY_SITES['request'](key))
        assert list(raml.endpoints['/a'].operations['post'].request.bodies) == [key.strip("'")]

    @pytest.mark.parametrize('key', ['a/b/c', 'application/ json', '/json', "'application/json; q'"])
    def test_each_bad_key_is_reported_once_at_itself(self, workspace, key):
        # Beside a good key, which is not reported: the check is per key.
        text = BODY_SITES['request'](key).replace('body:\n', 'body:\n      text/plain: string\n')
        error = fails(workspace, text)
        assert error is not None
        bad = [frame for frame in traces(error) if frame.message == 'invalid media type']
        assert [(frame.info, frame.position.line) for frame in bad] == [
            ({'media type': key.strip("'")}, API.count('\n') + 5)
        ]

    @staticmethod
    def _beside(site: str, sibling: str) -> str:
        """A bad key first, then `sibling` and a plain body, at `site`."""
        head = (
            '/a:\n  post:\n    body:\n'
            if site == 'request'
            else '/a:\n  post:\n    responses:\n      200:\n        body:\n'
        )
        indent = ' ' * (6 if site == 'request' else 10)
        return head + ''.join(
            f'{indent}{pair}\n' for pair in ('a/b/c: string', f'application/json: {sibling}', 'text/plain: string')
        )

    @pytest.mark.parametrize('site', ['request', 'response'])
    def test_a_bad_key_and_a_failing_sibling_are_both_reported(self, workspace, site):
        # Each body is decoded on its own: neither error hides the other, in
        # either order of failure.
        error = fails(workspace, self._beside(site, '{type: string, minLength: x}'))
        assert error is not None
        reported = [message for message, _ in frames(error)]
        assert 'invalid media type' in reported
        assert 'expected an integer value' in reported

    @pytest.mark.parametrize('site', ['request', 'response'])
    def test_a_bad_key_keeps_its_valid_siblings_in_the_lenient_model(self, workspace, site):
        # `Missing` is kept for P7 to judge; the parse stops at the failing
        # pass, so in this model it stays unresolved and unreported.
        raml, error = workspace.lenient_document(API + self._beside(site, 'Missing'))
        assert error is not None
        operation = raml.endpoints['/a'].operations['post']
        holder = operation.request if site == 'request' else operation.responses['200']
        assert list(holder.bodies) == ['application/json', 'text/plain']
        marked = operation if site == 'request' else holder
        assert raml.broken[marked.id].head.info == {'media type': 'a/b/c'}

    @pytest.mark.parametrize(
        ('kind', 'frame'),
        [
            ('Extension', ('invalid media type', {'media type': 'a/b/c'})),
            # An overlay cannot add a body at all, so that is what it reports.
            ('Overlay', ('not allowed in an overlay', {'field': 'a/b/c', 'change': 'added'})),
        ],
    )
    def test_a_body_key_an_extension_or_overlay_adds_is_refused(self, workspace, kind, frame):
        root = workspace(
            {
                'api.raml': API + '/a:\n  post:\n    body:\n      application/json: string\n',
                'ext.raml': f'#%RAML 1.0 {kind}\nextends: api.raml\n/a:\n  post:\n    body:\n      a/b/c: string\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            workspace.parse(root / 'ext.raml')
        assert frame in frames(caught.value)


class TestUriParameters:
    def test_an_undeclared_variable_is_synthesised_as_a_required_string(self, workspace):
        raml = parse(workspace, '/users/{id}:\n')
        prop = raml.endpoints['/users/{id}'].uri_parameters['id']
        assert prop.required
        assert prop.base.type == 'string'
        assert prop.synthesized

    def test_a_declared_parameter_wins(self, workspace):
        raml = parse(workspace, '/users/{id}:\n  uriParameters:\n    id: integer\n')
        parameter = raml.endpoints['/users/{id}'].uri_parameters['id']
        assert parameter.base.type == 'integer'
        assert not parameter.synthesized

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
        with pytest.raises(RamlError) as caught:
            workspace.document(
                API + '/users:\n  post:\n    body:\n      application/json:\n'
                '        type: integer\n        example: notanumber\n',
                ParseOptions(validate=True, unwrap=True),
            )
        assert 'invalid example' in {t.message for c in caught.value.chains() for t in c}


class TestAnnotationTargets:
    """The five annotation sites endpoint decoding establishes (docs/09 § B4).

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
        assert workspace.parse(root / 'lib.raml').endpoints == {}


class TestParameterEntity:
    """docs/05 § 4: a bound parameter is an entity, a property is a record."""

    def test_each_map_records_the_binding_it_was_declared_under(self, workspace):
        raml = parse(
            workspace,
            """
/items/{itemId}:
  get:
    headers:
      X-Trace: string
    queryParameters:
      page: integer
    responses:
      200:
        headers:
          X-Total: integer
""",
        )
        endpoint = raml.endpoints['/items/{itemId}']
        operation = endpoint.operations['get']
        assert endpoint.uri_parameters['itemId'].binding == 'uri'
        assert operation.request.headers['X-Trace'].binding == 'header'
        assert operation.request.query_parameters['page'].binding == 'query'
        assert operation.responses['200'].headers['X-Total'].binding == 'header'

    def test_name_base_and_required_read_through_to_the_property(self, workspace):
        raml = parse(workspace, '\n/items:\n  get:\n    queryParameters:\n      page?: integer\n')
        param = raml.endpoints['/items'].operations['get'].request.query_parameters['page']
        assert (param.name, param.required) == ('page', False)
        assert param.base is param.declaration.base
        assert param.base.type == 'integer'

    def test_a_parameter_carries_the_position_of_its_key(self, workspace):
        raml = parse(workspace, '\n/items:\n  get:\n    queryParameters:\n      page: integer\n')
        param = raml.endpoints['/items'].operations['get'].request.query_parameters['page']
        assert param.key_pos.is_known
        # The property it holds has nowhere to put this, which is why the
        # parameter exists (docs/05 § 4).
        assert not hasattr(param.declaration, 'key_pos')

    def test_an_inherited_uri_parameter_is_one_object_not_a_copy(self, workspace):
        """docs/08 § 6.2: the rewrite is `{**inherited, **own}`."""
        raml = parse(workspace, '\n/items/{itemId}:\n  /reviews/{reviewId}:\n    get:\n')
        parent = raml.endpoints['/items/{itemId}']
        child = parent.endpoints['/reviews/{reviewId}']
        assert child.uri_parameters['itemId'] is parent.uri_parameters['itemId']
        # Its own is not shared, and path order puts the ancestor's first.
        assert list(child.uri_parameters) == ['itemId', 'reviewId']
        assert 'reviewId' not in parent.uri_parameters

    def test_base_uri_parameters_bind_as_uri(self, workspace):
        raml = workspace.document(
            '#%RAML 1.0\ntitle: T\nbaseUri: http://{host}.example.test\nbaseUriParameters:\n  host:\n    type: string\n'
        )
        assert raml.entry_point.base_uri_parameters['host'].binding == 'uri'
