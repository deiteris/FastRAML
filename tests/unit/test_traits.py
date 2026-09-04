"""Traits: the four priority classes, and which file a merged node came from.

docs/08-templates-and-endpoints.md sections 5.2 and 6. The priority tests are
the obvious half. The provenance tests are the half that matters more, because
resolving a trait-contributed type name in the wrong namespace *still parses* —
it is the one failure in this parser that produces a model rather than an error.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from pyraml import ParseOptions, RamlError, parse_from_path

API = '#%RAML 1.0\ntitle: T\nmediaType: application/json\n'


def parse(root, name: str = 'api.raml'):
    return parse_from_path(root / name, ParseOptions())


def operation(raml, uri: str, method: str):
    return raml.endpoints[uri].operations[method]


class TestApplication:
    def test_a_trait_body_is_merged_under_the_method(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  paged:\n    queryParameters:\n      page: integer\n'
                + '/users:\n  get:\n    is: [paged]\n    description: mine\n'
            }
        )
        get = operation(parse(root), '/users', 'get')
        assert list(get.request.query_parameters) == ['page']
        assert get.description.value == 'mine'

    def test_the_method_wins_where_both_declare(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  paged:\n    description: theirs\n'
                + '/users:\n  get:\n    is: [paged]\n    description: mine\n'
            }
        )
        assert operation(parse(root), '/users', 'get').description.value == 'mine'

    def test_a_resource_level_trait_reaches_every_method(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  paged:\n    description: from the trait\n'
                + '/users:\n  is: [paged]\n  get:\n  post:\n'
            }
        )
        raml = parse(root)
        for method in ('get', 'post'):
            assert operation(raml, '/users', method).description.value == 'from the trait'

    def test_a_trait_applies_exactly_once_however_many_times_it_is_named(self, workspace):
        # Named on the method and on the resource. `enum` unions, so a second
        # application would be visible as a duplicated member.
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    queryParameters:\n      q:\n        enum: [a]\n'
                + '/users:\n  is: [t]\n  get:\n    is: [t]\n'
            }
        )
        query = operation(parse(root), '/users', 'get').request.query_parameters['q']
        assert [member.raw for member in query.base.enum] == ['a']

    def test_the_closest_occurrence_wins(self, workspace):
        # Spec section Effect on Collections: "priority is given to the trait in
        # closest proximity to the target method or resource". The method's
        # parameters are the ones substituted; the resource's application of the
        # same name is dropped.
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    description: <<who>>\n'
                + '/users:\n  is: [{t: {who: resource}}]\n  get:\n    is: [{t: {who: method}}]\n'
            }
        )
        assert operation(parse(root), '/users', 'get').description.value == 'method'


class TestParameters:
    def test_a_parameter_is_substituted(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    description: about <<what>>\n'
                + '/users:\n  get:\n    is: [{t: {what: users}}]\n'
            }
        )
        assert operation(parse(root), '/users', 'get').description.value == 'about users'

    def test_the_reserved_parameters_are_injected(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    description: <<methodName>> <<resourcePath>> <<resourcePathName>>\n'
                + '/users/{id}:\n  get:\n    is: [t]\n'
            }
        )
        assert operation(parse(root), '/users/{id}', 'get').description.value == 'get /users/{id} users'

    def test_an_action_transforms_the_value(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    description: <<resourcePathName | !singularize>>\n'
                + '/users:\n  get:\n    is: [t]\n'
            }
        )
        assert operation(parse(root), '/users', 'get').description.value == 'user'

    def test_an_undeclared_parameter_is_rejected(self, workspace):
        root = workspace(
            {'api.raml': API + 'traits:\n  t:\n    description: d\n/users:\n  get:\n    is: [{t: {nope: 1}}]\n'}
        )
        with pytest.raises(RamlError) as caught:
            parse(root)
        assert 'unexpected parameter' in str(caught.value)

    def test_a_missing_parameter_is_rejected(self, workspace):
        root = workspace({'api.raml': API + 'traits:\n  t:\n    description: <<what>>\n/users:\n  get:\n    is: [t]\n'})
        with pytest.raises(RamlError) as caught:
            parse(root)
        assert 'missing required parameter' in str(caught.value)

    def test_an_unresolvable_trait_names_itself(self, workspace):
        root = workspace({'api.raml': API + '/users:\n  get:\n    is: [nowhere]\n'})
        with pytest.raises(RamlError) as caught:
            parse(root)
        assert caught.value.head.info == {'trait': 'nowhere'}


class TestProvenance:
    """Which namespace a merged node's type names resolve in (docs/08 § 6.1)."""

    #: The example docs/08 § 6.1 is written around: one merged operation whose
    #: tree holds nodes authored in three files.
    THREE_WAY: ClassVar[dict[str, str]] = {
        'api.raml': API
        + 'uses:\n  types: types.raml\n'
        + 'traits:\n  paged: !include traits/paged.raml\n'
        + '/items:\n  get:\n    is: [{paged: {responseType: types.PagedResult}}]\n',
        'traits/paged.raml': (
            '#%RAML 1.0 Trait\n'
            'uses:\n  models: ../models.raml\n'
            'responses:\n'
            '  200:\n'
            '    body:\n'
            '      application/json:\n'
            '        type: <<responseType>>\n'
            '        properties:\n'
            '          total: models.Count\n'
        ),
        'types.raml': '#%RAML 1.0 Library\ntypes:\n  PagedResult:\n    properties:\n      page: integer\n',
        'models.raml': '#%RAML 1.0 Library\ntypes:\n  Count: integer\n',
    }

    @pytest.fixture
    def three_way_body(self, workspace):
        raml = parse(workspace(dict(self.THREE_WAY)))
        return raml.endpoints['/items'].operations['get'].responses['200'].bodies['application/json']

    def test_all_three_namespaces_resolve_at_once(self, three_way_body):
        # `models.Count` came from the trait and must resolve through the trait
        # fragment's `uses:`; `types.PagedResult` was substituted by the caller
        # and must resolve through api.raml's. A single "current file" is wrong
        # for at least one of them, whichever one it is. A bare name is an alias
        # rather than an inheritance (docs/07 § 3.6), hence the two spellings.
        assert [inherited.name for inherited in three_way_body.shape.inherits] == ['PagedResult']
        total = three_way_body.shape.shape.properties['total'].base
        assert total.alias.name == 'Count'
        assert total.alias.location.endswith('models.raml')

    def test_a_trait_contributed_shape_is_attributed_to_the_trait_file(self, three_way_body):
        assert three_way_body.shape.location.endswith('traits/paged.raml')

    def test_static_trait_content_resolves_in_the_trait_not_the_caller(self, workspace):
        # api.raml declares a `Thing` of its own. The trait's unqualified
        # `Thing` must not find it: the trait fragment declares no such name and
        # imports none, so this is an error rather than a silent wrong binding.
        root = workspace(
            {
                'api.raml': API
                + 'types:\n  Thing: string\n'
                + 'traits:\n  t: !include t.raml\n'
                + '/items:\n  get:\n    is: [t]\n',
                't.raml': '#%RAML 1.0 Trait\nbody:\n  application/json:\n    type: Thing\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse(root)
        assert 'Thing' in str(caught.value)

    def test_a_caller_substituted_type_resolves_in_the_caller(self, workspace):
        # The mirror image: the *caller's* `Thing`, handed to a trait that has
        # no idea what it is, still resolves.
        root = workspace(
            {
                'api.raml': API
                + 'types:\n  Thing: string\n'
                + 'traits:\n  t: !include t.raml\n'
                + '/items:\n  get:\n    is: [{t: {kind: Thing}}]\n',
                't.raml': '#%RAML 1.0 Trait\nbody:\n  application/json:\n    type: <<kind>>\n',
            }
        )
        body = parse(root).endpoints['/items'].operations['get'].request.bodies['application/json']
        assert [inherited.name for inherited in body.shape.inherits] == ['Thing']

    def test_a_trait_contributed_shape_joins_the_later_passes(self, workspace):
        # `fragment_typedefs` is the only index P9 and P10 iterate. A shape a
        # trait contributed that misses it is never flattened and never
        # validated, and nothing else in the suite would notice.
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    body:\n      application/json:\n        type: string\n        example: 1\n'
                + '/items:\n  get:\n    is: [t]\n'
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
        assert 'example' in str(caught.value)
