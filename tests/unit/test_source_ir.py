"""Stage 1 — the endpoint IR.

docs/08-templates-and-endpoints.md § 2.1. What this pins is the *split*:
exactly four kinds of key are consumed and everything else survives untouched,
because P4's merge is defined on the YAML tree and needs one to merge into.
"""

from __future__ import annotations

import pytest

from fastraml import RamlError
from fastraml.parser.source_ir import METHODS, make_source_endpoint
from fastraml.registry import Raml
from fastraml.yamlnode import compose, pairs

LOCATION = 'file:///a.raml'


def source(text: str, *, parent_uri: str = ''):
    """Stage-1 decode of a one-resource document."""
    key, value = next(iter(pairs(compose(text, uri=LOCATION))))
    return make_source_endpoint(Raml(workspace_root_uri='file:///'), key, value, LOCATION, parent_uri=parent_uri)


def keys(node) -> list[str]:
    return [] if node is None else [child.value for child in node.content[::2]]


class TestDirectivesAreConsumed:
    def test_type_is_and_secured_by_come_off_a_resource(self):
        endpoint = source('/users:\n  type: collection\n  is: [paged]\n  securedBy: [oauth]\n  description: d\n')
        assert endpoint.resource_type.name == 'collection'
        assert [trait.name for trait in endpoint.traits] == ['paged']
        assert [scheme.name for scheme in endpoint.secured_by] == ['oauth']
        assert keys(endpoint.body) == ['description'], 'only the directives are consumed'

    def test_a_method_takes_two_of_the_three(self):
        endpoint = source('/users:\n  get:\n    is: [paged]\n    securedBy: [oauth]\n    description: d\n')
        get = endpoint.operations['get']
        assert [trait.name for trait in get.traits] == ['paged']
        assert [scheme.name for scheme in get.secured_by] == ['oauth']
        assert keys(get.body) == ['description']

    def test_a_reference_may_carry_parameters(self):
        endpoint = source('/users:\n  type: { collection: { itemType: User } }\n')
        assert endpoint.resource_type.name == 'collection'
        assert list(endpoint.resource_type.params) == ['itemType']

    def test_a_bare_name_carries_none(self):
        endpoint = source('/users:\n  is: [paged]\n')
        assert endpoint.traits[0].params == {}

    def test_a_single_trait_must_still_be_a_sequence(self):
        # Spec section Traits: "The value MUST be an array of any number of
        # elements". `is: paged` reads as though it should work, and go-raml
        # accepts it — `Traits/is-node-format/invalid-is-single-value.raml` is
        # the fixture that says otherwise.
        with pytest.raises(RamlError) as caught:
            source('/users:\n  is: paged\n')
        assert 'is must be a sequence' in str(caught.value)

    def test_a_resource_type_may_not_be_a_sequence(self):
        # A sequence in a `type:` position is multiple inheritance for a *type
        # declaration*; a resource has no such form.
        with pytest.raises(RamlError) as caught:
            source('/users:\n  type: [a, b]\n')
        assert 'resource type must be a single reference' in str(caught.value)


class TestSecuredByNull:
    """`securedBy: [null]` is not the same as no `securedBy:` at all."""

    def test_a_null_entry_is_kept(self):
        endpoint = source('/users:\n  securedBy: [~]\n')
        assert endpoint.secured_by[0].is_null_scheme
        assert endpoint.explicit_secured_by

    def test_silence_is_distinguishable_from_an_empty_sequence(self):
        assert not source('/users:\n  get:\n').explicit_secured_by
        assert source('/users:\n  securedBy: []\n').explicit_secured_by

    def test_a_trait_may_not_be_null(self):
        with pytest.raises(RamlError):
            source('/users:\n  is: [~]\n')


class TestStructure:
    def test_methods_and_subresources_recurse(self):
        endpoint = source('/users:\n  get:\n  post:\n  /{id}:\n    delete:\n')
        assert list(endpoint.operations) == ['get', 'post']
        assert list(endpoint.endpoints) == ['/{id}']
        assert list(endpoint.endpoints['/{id}'].operations) == ['delete']

    def test_full_uri_concatenates_ancestors(self):
        endpoint = source('/users:\n  /{id}:\n    /photos:\n')
        assert endpoint.full_uri == '/users'
        assert endpoint.endpoints['/{id}'].full_uri == '/users/{id}'
        assert endpoint.endpoints['/{id}'].endpoints['/photos'].full_uri == '/users/{id}/photos'

    def test_the_base_uri_is_not_prepended(self):
        # docs/08 § 6.1: the base is exposed separately on the API.
        assert source('/users:\n', parent_uri='').full_uri == '/users'

    def test_declaration_order_is_preserved(self):
        endpoint = source('/users:\n  post:\n  get:\n  /z:\n  /a:\n')
        assert list(endpoint.operations) == ['post', 'get']
        assert list(endpoint.endpoints) == ['/z', '/a']

    def test_an_empty_resource_is_legal(self):
        endpoint = source('/users:\n')
        assert endpoint.body is None
        assert endpoint.operations == {}

    def test_an_empty_method_is_legal(self):
        assert source('/users:\n  get:\n').operations['get'].body is None

    @pytest.mark.parametrize('method', sorted(METHODS))
    def test_every_http_method_is_recognised(self, method):
        assert list(source(f'/users:\n  {method}:\n').operations) == [method]

    def test_a_duplicate_method_is_rejected(self):
        # YAML duplicate keys are caught earlier; this guards the IR's own map.
        endpoint = source('/users:\n  get:\n    description: a\n')
        assert list(endpoint.operations) == ['get']


class TestRetainedBody:
    def test_the_retained_node_is_not_the_document_s_own(self):
        # P4 merges into this, and the merge must not reach the document.
        text = '/users:\n  description: d\n'
        key, value = next(iter(pairs(compose(text, uri=LOCATION))))
        endpoint = make_source_endpoint(Raml(workspace_root_uri='file:///'), key, value, LOCATION)
        assert endpoint.body is not value
        assert endpoint.body.content is not value.content

    def test_it_carries_the_original_s_position(self):
        endpoint = source('/users:\n  description: d\n')
        assert endpoint.body.line == 2

    def test_nothing_retained_means_none_rather_than_an_empty_mapping(self):
        assert source('/users:\n  get:\n').body is None

    def test_everything_type_bearing_survives(self):
        endpoint = source(
            '/users:\n  uriParameters:\n    id: string\n'
            '  get:\n    queryParameters:\n      q: string\n'
            '    body: string\n    responses:\n      200:\n'
        )
        assert keys(endpoint.body) == ['uriParameters']
        assert keys(endpoint.operations['get'].body) == ['queryParameters', 'body', 'responses']


class TestErrors:
    def test_a_non_mapping_resource_is_rejected(self):
        with pytest.raises(RamlError) as caught:
            source('/users: notamapping\n')
        assert 'resource must be a mapping' in str(caught.value)

    def test_sibling_errors_accumulate(self):
        # One malformed method must not hide the rest of the resource.
        with pytest.raises(RamlError) as caught:
            source('/users:\n  get: 1\n  post: 2\n')
        methods = {trace.info.get('method') for chain in caught.value.chains() for trace in chain if trace.info}
        assert methods == {'get', 'post'}
