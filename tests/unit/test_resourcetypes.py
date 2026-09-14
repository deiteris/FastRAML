"""Resource types: the six compile steps, and the priority split they create.

docs/08-templates-and-endpoints.md section 5.1. Two of these encode decisions
that cost a session each: optional methods are filtered *before* the required
variables are recollected, and a resource type's own `is:` entries become
`rt_traits` rather than `traits`, which is what keeps them behind the
resource's own.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, RamlError, parse_from_path

API = '#%RAML 1.0\ntitle: T\nmediaType: application/json\n'


def parse(root, name: str = 'api.raml'):
    return parse_from_path(root / name, ParseOptions())


class TestApplication:
    def test_a_method_the_resource_lacks_is_grafted(self, workspace):
        root = workspace(
            {'api.raml': API + 'resourceTypes:\n  base:\n    get:\n      description: d\n/users:\n  type: base\n'}
        )
        endpoint = parse(root).endpoints['/users']
        assert list(endpoint.operations) == ['get']
        assert endpoint.operations['get'].description.value == 'd'

    def test_a_method_the_resource_declares_merges_and_wins(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'resourceTypes:\n  base:\n    get:\n      description: theirs\n      displayName: kept\n'
                + '/users:\n  type: base\n  get:\n    description: mine\n'
            }
        )
        get = parse(root).endpoints['/users'].operations['get']
        assert get.description.value == 'mine'
        assert get.display_name.value == 'kept'

    def test_endpoint_level_facets_merge_too(self, workspace):
        root = workspace(
            {'api.raml': API + 'resourceTypes:\n  base:\n    description: from the type\n' + '/users:\n  type: base\n'}
        )
        assert parse(root).endpoints['/users'].description.value == 'from the type'

    def test_a_chain_applies_with_the_closest_declaration_winning(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'resourceTypes:\n'
                + '  grandparent:\n    description: grandparent\n    displayName: from grandparent\n'
                + '  parent:\n    type: grandparent\n    description: parent\n'
                + '/users:\n  type: parent\n'
            }
        )
        endpoint = parse(root).endpoints['/users']
        assert endpoint.description.value == 'parent'
        assert endpoint.display_name.value == 'from grandparent'

    def test_a_cycle_terminates(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'resourceTypes:\n  a:\n    type: b\n    description: a\n  b:\n    type: a\n    description: b\n'
                + '/users:\n  type: a\n'
            }
        )
        assert parse(root).endpoints['/users'].description.value == 'a'

    def test_an_unresolvable_resource_type_names_itself(self, workspace):
        root = workspace({'api.raml': API + '/users:\n  type: nowhere\n'})
        with pytest.raises(RamlError) as caught:
            parse(root)
        assert caught.value.head.info == {'resourceType': 'nowhere'}


class TestOptionalMethods:
    """Spec section Declaring HTTP Methods as Optional, and its own example."""

    CORP = API + (
        'resourceTypes:\n'
        '  corpResource:\n'
        '    post?:\n'
        '      description: <<TextAboutPost>>\n'
        '    get:\n'
        '      description: <<TextAboutGet>>\n'
    )

    def test_an_optional_method_the_resource_lacks_is_dropped(self, workspace):
        root = workspace(
            {'api.raml': self.CORP + '/queues:\n  type: { corpResource: { TextAboutGet: about get } }\n  get:\n'}
        )
        assert list(parse(root).endpoints['/queues'].operations) == ['get']

    def test_its_variables_are_not_required(self, workspace):
        # The point of filtering before recollecting: `/queues` has no `post`,
        # so `<<TextAboutPost>>` is unreachable and must not be demanded.
        # go-raml demands it (KNOWN-ISSUES.md entry 6).
        root = workspace(
            {'api.raml': self.CORP + '/queues:\n  type: { corpResource: { TextAboutGet: about get } }\n  get:\n'}
        )
        assert parse(root).endpoints['/queues'].operations['get'].description.value == 'about get'

    def test_a_sibling_variable_is_still_substituted(self, workspace):
        # The second half of the same fault: with a stale index, filtering out
        # `post` leaves `<<TextAboutGet>>` looking up the wrong entry and
        # reaching the model unsubstituted.
        root = workspace(
            {
                'api.raml': self.CORP
                + '/queues:\n  type: { corpResource: { TextAboutGet: about get, TextAboutPost: unused } }\n  get:\n'
            }
        )
        assert parse(root).endpoints['/queues'].operations['get'].description.value == 'about get'

    def test_an_optional_method_the_resource_declares_is_applied(self, workspace):
        root = workspace(
            {
                'api.raml': self.CORP
                + '/queues:\n'
                + '  type: { corpResource: { TextAboutGet: g, TextAboutPost: p } }\n'
                + '  get:\n  post:\n'
            }
        )
        endpoint = parse(root).endpoints['/queues']
        assert endpoint.operations['post'].description.value == 'p'

    def test_a_declaration_error_names_the_offending_key(self, workspace):
        root = workspace({'api.raml': API + 'resourceTypes:\n  base:\n    nonsense:\n      description: d\n'})
        with pytest.raises(RamlError) as caught:
            parse(root)
        assert 'resource type method must be an HTTP method' in str(caught.value)


class TestTraitPriority:
    """The `traits` / `rt_traits` split (docs/08 sections 5.1 and 5.2)."""

    FOUR_CLASSES = API + (
        'traits:\n'
        '  t:\n    description: <<who>>\n'
        'resourceTypes:\n'
        '  rt:\n'
        '    is: [{t: {who: rt-resource}}]\n'
        '    get:\n      is: [{t: {who: rt-method}}]\n'
    )

    def test_a_resource_type_method_trait_beats_a_resource_type_resource_trait(self, workspace):
        root = workspace({'api.raml': self.FOUR_CLASSES + '/users:\n  type: rt\n  get:\n'})
        assert parse(root).endpoints['/users'].operations['get'].description.value == 'rt-method'

    def test_the_resources_own_trait_beats_both(self, workspace):
        root = workspace(
            {'api.raml': self.FOUR_CLASSES + '/users:\n  type: rt\n  is: [{t: {who: resource}}]\n  get:\n'}
        )
        assert parse(root).endpoints['/users'].operations['get'].description.value == 'resource'

    def test_the_methods_own_trait_beats_everything(self, workspace):
        root = workspace(
            {
                'api.raml': self.FOUR_CLASSES
                + '/users:\n  type: rt\n  is: [{t: {who: resource}}]\n  get:\n    is: [{t: {who: method}}]\n'
            }
        )
        assert parse(root).endpoints['/users'].operations['get'].description.value == 'method'

    def test_a_trait_named_only_by_the_resource_type_still_applies(self, workspace):
        root = workspace({'api.raml': self.FOUR_CLASSES + '/users:\n  type: rt\n'})
        assert parse(root).endpoints['/users'].operations['get'].description.value == 'rt-method'


class TestProvenance:
    def test_a_fragment_resource_type_resolves_in_its_own_namespace(self, workspace):
        # Deviation D4: an `!include`d template is self-contained. Its `Thing`
        # is the library it imports, not the identically named type the
        # applying document happens to declare.
        root = workspace(
            {
                'api.raml': API
                + 'types:\n  Thing: string\n'
                + 'resourceTypes:\n  rt: !include rt.raml\n'
                + '/users:\n  type: rt\n',
                'rt.raml': (
                    '#%RAML 1.0 ResourceType\n'
                    'uses:\n  models: models.raml\n'
                    'get:\n  body:\n    application/json:\n      type: models.Thing\n'
                ),
                'models.raml': '#%RAML 1.0 Library\ntypes:\n  Thing: integer\n',
            }
        )
        body = parse(root).endpoints['/users'].operations['get'].request.bodies['application/json']
        assert body.shape.inherits[0].location.endswith('models.raml')
        assert body.shape.type == 'integer', "the caller's string Thing must not win"

    def test_a_grafted_operation_is_attributed_to_the_template_file(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'resourceTypes:\n  rt: !include rt.raml\n/users:\n  type: rt\n',
                'rt.raml': '#%RAML 1.0 ResourceType\nget:\n  body:\n    application/json:\n      type: string\n',
            }
        )
        body = parse(root).endpoints['/users'].operations['get'].request.bodies['application/json']
        assert body.shape.location.endswith('rt.raml')

    def test_a_resource_type_contributed_shape_joins_the_later_passes(self, workspace):
        root = workspace(
            {
                'api.raml': API
                + 'resourceTypes:\n  rt:\n    get:\n      body:\n'
                + '        application/json:\n          type: string\n          example: 1\n'
                + '/users:\n  type: rt\n'
            }
        )
        with pytest.raises(RamlError) as caught:
            parse_from_path(root / 'api.raml', ParseOptions(unwrap=True, validate=True))
        assert 'example' in str(caught.value)
