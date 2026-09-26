"""Traits: the four priority classes, and which file a merged node came from.

docs/08-templates-and-endpoints.md § 3.2 and § 4. The priority tests are
the obvious half. The provenance tests are the half that matters more, because
resolving a trait-contributed type name in the wrong namespace *still parses* —
it is the one failure in this parser that produces a model rather than an error.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path

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


class TestEveryReferenceIsBound:
    """docs/08 § 3.2: an `is:` entry names a trait whether or not it is applied.

    The name rule skips the farther of two same-named references, and a
    resource with no methods applies nothing. Either reference used to stay
    unbound: a misspelt name went unreported, and a library-qualified one left
    the graph an unresolved node, which lint refuses.
    """

    LIBRARY: ClassVar[str] = '#%RAML 1.0 Library\ntraits:\n  drm:\n    description: d\n'

    def test_a_reference_the_name_rule_skipped_is_bound(self, workspace):
        root = workspace(
            {
                'lib.raml': self.LIBRARY,
                'api.raml': API + 'uses:\n  l: lib.raml\n/a:\n  is: [l.drm]\n  get:\n    is: [l.drm]\n',
            }
        )
        endpoint = parse(root).endpoints['/a']
        assert endpoint.traits[0].resolved is not None
        assert endpoint.traits[0].resolved is endpoint.operations['get'].traits[0].resolved

    @pytest.mark.parametrize(
        'body',
        [
            pytest.param('/a:\n  is: [l.drm]\n', id='resource'),
            pytest.param('resourceTypes:\n  rt:\n    is: [l.drm]\n/a:\n  type: rt\n', id='resource type'),
        ],
    )
    def test_a_reference_on_a_resource_with_no_methods_is_bound(self, workspace, body):
        root = workspace({'lib.raml': self.LIBRARY, 'api.raml': API + 'uses:\n  l: lib.raml\n' + body})
        assert parse(root).endpoints['/a'].traits[0].resolved is not None

    @pytest.mark.parametrize(
        'body',
        [
            pytest.param('/a:\n  is: [nosuch]\n', id='resource'),
            pytest.param('resourceTypes:\n  rt:\n    is: [nosuch]\n/a:\n  type: rt\n', id='resource type'),
            pytest.param('/a:\n  is: [nosuch]\n  get:\n', id='applied'),
        ],
    )
    def test_a_name_that_matches_nothing_is_reported_once(self, workspace, body):
        root = workspace({'api.raml': API + body})
        with pytest.raises(RamlError) as caught:
            parse(root)
        chains = list(caught.value.chains())
        assert [[frame.message for frame in chain][:2] for chain in chains] == [['apply trait', 'get trait definition']]
        assert chains[0][0].info == {'trait': 'nosuch'}


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
    """Which namespace a merged node's type names resolve in (docs/08 § 4.2)."""

    #: The example docs/08 § 4.2 is written around: one merged operation whose
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
        # rather than an inheritance (docs/07 § 3), hence the two spellings.
        assert [inherited.name for inherited in three_way_body.shape.inherits] == ['PagedResult']
        total = three_way_body.shape.shape.properties['total'].base
        assert total.alias.name == 'Count'
        assert total.alias.location.endswith('models.raml')

    def test_a_trait_contributed_shape_is_attributed_to_the_trait_file(self, three_way_body):
        assert three_way_body.shape.location.endswith('traits/paged.raml')

    def test_a_substituted_name_is_recorded_where_the_caller_wrote_it(self, three_way_body):
        # docs/08 § 5.1: the trait's scalar is `<<responseType>>`; the prefix and
        # the name are written in api.raml, in the `is:` entry.
        line = self.THREE_WAY['api.raml'].splitlines().index('    is: [{paged: {responseType: types.PagedResult}}]')
        column = len('    is: [{paged: {responseType: ') + 1
        refs = three_way_body.shape.type_expr_refs
        assert [(ref.location.rsplit('/', 1)[-1], ref.line, ref.column) for ref in refs] == [
            ('api.raml', line + 1, column),
            ('api.raml', line + 1, column + len('types.')),
        ]

    def test_the_record_of_substitutions_is_dropped_after_resolution(self, workspace):
        # Only P7 reads it; kept, it would hold every application's values.
        assert parse(workspace(dict(self.THREE_WAY))).substitutions == {}

    def test_a_substituted_name_that_resolves_nowhere_is_reported_where_it_was_written(self, workspace):
        files = dict(self.THREE_WAY)
        files['api.raml'] = files['api.raml'].replace('types.PagedResult', 'types.Nope')
        with pytest.raises(RamlError) as caught:
            parse(workspace(files))
        frames = {
            (frame.location.rsplit('/', 1)[-1], frame.position.line)
            for chain in caught.value.chains()
            for frame in chain
            if frame.info.get('type') == 'types.Nope'
        }
        assert frames == {
            ('api.raml', files['api.raml'].splitlines().index('    is: [{paged: {responseType: types.Nope}}]') + 1)
        }

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
