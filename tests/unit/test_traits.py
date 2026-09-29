"""Traits: the four priority classes, and which file a merged node came from.

docs/08-templates-and-endpoints.md § 3.2 and § 4. The priority tests are
the obvious half. The provenance tests are the half that matters more, because
resolving a trait-contributed type name in the wrong namespace *still parses* —
it is the one failure in this parser that produces a model rather than an error.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from fastraml import ParseOptions, RamlError

API = '#%RAML 1.0\ntitle: T\nmediaType: application/json\n'


@pytest.fixture
def workspace(memory_workspace):
    return memory_workspace


def parse(workspace, root, name: str = 'api.raml'):
    return workspace.parse(root / name)


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
        get = operation(parse(workspace, root), '/users', 'get')
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
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'mine'

    def test_a_resource_level_trait_reaches_every_method(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  paged:\n    description: from the trait\n'
                + '/users:\n  is: [paged]\n  get:\n  post:\n'
            }
        )
        raml = parse(workspace, root)
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
        query = operation(parse(workspace, root), '/users', 'get').request.query_parameters['q']
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
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'method'


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
        endpoint = parse(workspace, root).endpoints['/a']
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
        assert parse(workspace, root).endpoints['/a'].traits[0].resolved is not None

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
            parse(workspace, root)
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
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'about users'

    def test_the_reserved_parameters_are_injected(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    description: <<methodName>> <<resourcePath>> <<resourcePathName>>\n'
                + '/users/{id}:\n  get:\n    is: [t]\n'
            }
        )
        assert operation(parse(workspace, root), '/users/{id}', 'get').description.value == 'get /users/{id} users'

    def test_an_action_transforms_the_value(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  t:\n    description: <<resourcePathName | !singularize>>\n'
                + '/users:\n  get:\n    is: [t]\n'
            }
        )
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'user'

    def test_an_undeclared_parameter_is_rejected(self, workspace):
        root = workspace(
            {'api.raml': API + 'traits:\n  t:\n    description: d\n/users:\n  get:\n    is: [{t: {nope: 1}}]\n'}
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert 'unexpected parameter' in str(caught.value)

    def test_a_missing_parameter_is_rejected(self, workspace):
        root = workspace({'api.raml': API + 'traits:\n  t:\n    description: <<what>>\n/users:\n  get:\n    is: [t]\n'})
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert 'missing required parameter' in str(caught.value)

    @pytest.mark.parametrize('name', ['methodName', 'resourcePath', 'resourcePathName'])
    def test_a_reserved_parameter_the_caller_supplies_is_rejected(self, workspace, name):
        # Spec section Resource Type and Trait Parameters: the processor
        # provides it. Accepted, the injected value would silently win.
        root = workspace(
            {
                'api.raml': API
                + f'traits:\n  t:\n    description: <<{name}>>\n/users:\n  get:\n    is: [{{t: {{{name}: x}}}}]\n'
            }
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        (chain,) = caught.value.chains()
        assert [(frame.message, frame.info) for frame in chain] == [
            ('apply trait', {'trait': 't'}),
            ('reserved parameter', {'parameter': name}),
        ]

    def test_a_reserved_parameter_supplied_to_a_resource_type_is_rejected(self, workspace):
        root = workspace(
            {'api.raml': API + 'resourceTypes:\n  r:\n    get:\n/users:\n  type: {r: {resourcePath: x}}\n'}
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert [
            frame.info for chain in caught.value.chains() for frame in chain if frame.message == 'reserved parameter'
        ] == [{'parameter': 'resourcePath'}]

    def test_an_unresolvable_trait_names_itself(self, workspace):
        root = workspace({'api.raml': API + '/users:\n  get:\n    is: [nowhere]\n'})
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
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
        raml = parse(workspace, workspace(dict(self.THREE_WAY)))
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
        assert parse(workspace, workspace(dict(self.THREE_WAY))).substitutions == {}

    def test_a_substituted_name_that_resolves_nowhere_is_reported_where_it_was_written(self, workspace):
        files = dict(self.THREE_WAY)
        files['api.raml'] = files['api.raml'].replace('types.PagedResult', 'types.Nope')
        with pytest.raises(RamlError) as caught:
            parse(workspace, workspace(files))
        frames = {
            (frame.location.rsplit('/', 1)[-1], frame.position.line)
            for chain in caught.value.chains()
            for frame in chain
            if frame.info.get('type') == 'types.Nope'
        }
        assert frames == {
            ('api.raml', files['api.raml'].splitlines().index('    is: [{paged: {responseType: types.Nope}}]') + 1)
        }

    def test_a_substituted_annotation_name_that_resolves_nowhere_is_reported_where_it_was_written(self, workspace):
        files = dict(self.THREE_WAY)
        # A template's annotation key names what the caller supplied.
        files['api.raml'] = files['api.raml'].replace('PagedResult}', 'PagedResult, tag: nope}')
        files['traits/paged.raml'] = files['traits/paged.raml'].replace('responses:\n', '(<<tag>>): 1\nresponses:\n')
        with pytest.raises(RamlError) as caught:
            parse(workspace, workspace(files))
        frames = {
            (frame.location.rsplit('/', 1)[-1], frame.position.line, frame.position.column)
            for chain in caught.value.chains()
            for frame in chain
            if frame.info.get('annotation') == 'nope'
        }
        line = files['api.raml'].splitlines().index('    is: [{paged: {responseType: types.PagedResult, tag: nope}}]')
        assert frames == {('api.raml', line + 1, len('    is: [{paged: {responseType: types.PagedResult, tag: ') + 1)}

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
            parse(workspace, root)
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
        body = parse(workspace, root).endpoints['/items'].operations['get'].request.bodies['application/json']
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
            workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
        assert 'example' in str(caught.value)


class TestNested:
    """A trait's own `is:` (docs/08 § 3.2): spec section Algorithm of Merging
    Traits and Methods, one distance at a time, each trait applied once.
    """

    def test_a_trait_applies_the_traits_it_names(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  paged:\n    queryParameters:\n      page: integer\n'
                + '  listing:\n    is: [paged]\n    description: a list\n'
                + '/users:\n  get:\n    is: [listing]\n'
            }
        )
        get = operation(parse(workspace, root), '/users', 'get')
        assert (get.description.value, list(get.request.query_parameters)) == ('a list', ['page'])

    def test_the_nested_reference_is_kept_and_bound(self, workspace):
        # Retained as a `type:` or `is:` reference is, so a consumer can follow it.
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  paged:\n    description: d\n  listing:\n    is: [paged]\n'
                + '/users:\n  get:\n    is: [listing]\n'
            }
        )
        raml = parse(workspace, root)
        traits = operation(raml, '/users', 'get').traits
        assert [(ref.name, ref.resolved) for ref in traits] == [
            ('listing', raml.entry_point.traits['listing']),
            ('paged', raml.entry_point.traits['paged']),
        ]

    def test_a_parameter_reaches_the_nested_trait(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  named:\n    description: <<what>>\n'
                + '  listing:\n    is: [{named: {what: <<noun>>}}]\n'
                + '/users:\n  get:\n    is: [{listing: {noun: users}}]\n'
            }
        )
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'users'

    def test_a_trait_named_directly_beats_the_same_trait_nested(self, workspace):
        # The direct application is closer; the nested one is not applied at all.
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  named:\n    description: <<what>>\n'
                + '  listing:\n    is: [{named: {what: nested}}]\n'
                + '/users:\n  get:\n    is: [listing, {named: {what: direct}}]\n'
            }
        )
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'direct'

    def test_a_resources_trait_beats_a_methods_nested_trait(self, workspace):
        # Every trait the method, resource or resource type names is at distance
        # one; what they name is at distance two.
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  deep:\n    description: deep\n  listing:\n    is: [deep]\n'
                + '  owned:\n    description: resource\n'
                + '/users:\n  is: [owned]\n  get:\n    is: [listing]\n'
            }
        )
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'resource'

    def test_a_nested_trait_several_levels_down_is_applied(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  a:\n    is: [b]\n  b:\n    is: [c]\n  c:\n    description: c\n'
                + '/users:\n  get:\n    is: [a]\n'
            }
        )
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'c'

    def test_a_cycle_applies_each_trait_once(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'traits:\n  a:\n    is: [b]\n    description: a\n'
                + '  b:\n    is: [a]\n    queryParameters:\n      page: integer\n'
                + '/users:\n  get:\n    is: [a]\n'
            }
        )
        get = operation(parse(workspace, root), '/users', 'get')
        assert (get.description.value, list(get.request.query_parameters)) == ('a', ['page'])

    def test_a_nested_name_resolves_where_the_trait_is_declared(self, workspace):
        # `paged` is the library's: api.raml would have to write `lib.paged`.
        root = workspace(
            {
                'api.raml': API + 'uses:\n  lib: lib.raml\n/users:\n  get:\n    is: [lib.listing]\n',
                'lib.raml': '#%RAML 1.0 Library\ntraits:\n  paged:\n    description: paged\n'
                '  listing:\n    is: [paged]\n',
            }
        )
        assert operation(parse(workspace, root), '/users', 'get').description.value == 'paged'

    def test_an_unresolvable_nested_name_is_reported_where_it_is_written(self, workspace):
        root = workspace(
            {'api.raml': API + 'traits:\n  listing:\n    is: [nowhere]\n/users:\n  get:\n    is: [listing]\n'}
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert caught.value.head.info == {'trait': 'nowhere'}
        assert _where(caught.value, 'apply trait') == [('api.raml', 6, 10)]

    def test_a_nested_traits_securedby_applies(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'securitySchemes:\n  basic:\n    type: Basic Authentication\n'
                + 'traits:\n  secured:\n    securedBy: [basic]\n  listing:\n    is: [secured]\n'
                + '/users:\n  get:\n    is: [listing]\n'
            }
        )
        assert [s.name for s in operation(parse(workspace, root), '/users', 'get').secured_by] == ['basic']


def _where(error: RamlError, message: str) -> list[tuple[str, int, int]]:
    """File, line and column of every frame carrying `message`."""
    return [
        (frame.location.rsplit('/', 1)[-1], frame.position.line, frame.position.column)
        for chain in error.chains()
        for frame in chain
        if frame.message == message and frame.position is not None
    ]


class TestLocation:
    """A pair a template grafted is located in the file the template was written in.

    Its position is always the template's, so naming the operation's file
    instead points at a line of a file that does not hold it.
    """

    INCLUDED = 'traits:\n  t: !include t.raml\n/items:\n  get:\n    is: [t]\n'

    def test_a_top_level_key_of_a_trait_fragment_is_located_in_the_fragment(self, workspace):
        root = workspace({'api.raml': API + self.INCLUDED, 't.raml': '#%RAML 1.0 Trait\nbogus: 1\n'})
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert _where(caught.value, 'unknown field') == [('t.raml', 2, 1)]

    def test_a_key_merged_beside_the_methods_own_is_located_in_the_fragment(self, workspace):
        # The method has a body, so the trait's `queryString` is merged in
        # beside its `queryParameters`, and the error is at the one written second.
        root = workspace(
            {
                'api.raml': API + self.INCLUDED + '    queryParameters:\n      page: integer\n',
                't.raml': '#%RAML 1.0 Trait\nqueryString:\n  properties:\n    q: string\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert _where(caught.value, 'queryString and queryParameters are mutually exclusive') == [('t.raml', 2, 1)]

    def test_the_methods_own_key_stays_in_the_api(self, workspace):
        root = workspace(
            {
                'api.raml': API + self.INCLUDED + '    bogus: 1\n',
                't.raml': '#%RAML 1.0 Trait\ndescription: d\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert _where(caught.value, 'unknown field') == [('api.raml', 9, 5)]

    def test_a_library_traits_facet_is_located_in_the_library(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'uses:\n  l: lib.raml\n/items:\n  get:\n    is: [l.t]\n',
                'lib.raml': '#%RAML 1.0 Library\ntraits:\n  t:\n    description: <<methodName>> items\n',
            }
        )
        description = operation(parse(workspace, root), '/items', 'get').description
        assert (description.value, description.location.rsplit('/', 1)[-1]) == ('get items', 'lib.raml')

    def test_a_library_resource_types_facet_is_located_in_the_library(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'uses:\n  l: lib.raml\n/items:\n  type: l.r\n',
                'lib.raml': '#%RAML 1.0 Library\nresourceTypes:\n  r:\n    description: [1]\n',
            }
        )
        with pytest.raises(RamlError) as caught:
            parse(workspace, root)
        assert [where[0] for where in _where(caught.value, 'expected scalar or mapping node')] == ['lib.raml']
