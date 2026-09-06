"""The effective document as an addressed tree — docs/16 § 11, docs/14 law 15.

The projection is pinned whole by the golden layer. What is asserted here is the
property the goldens cannot see: that every reference it emits **resolves**, and
resolves to the node the graph put at the same address.

That is the check on Phase A's addressable set. A reference to something the
walk never reached would come out as `null`, which reads exactly like "there was
nothing to point at" — the silent failure the whole addressing scheme exists to
remove.
"""

from __future__ import annotations

import pytest

from pyraml import ParseOptions, parse_from_path
from pyraml.effective import effective, positions_of
from pyraml.graph import build_graph

#: Exercises each of the four cross-references at once: `inherits`, an alias
#: under an array, a recursion head, and an applied annotation.
API = """#%RAML 1.0
title: Refs
annotationTypes:
  tier: string
types:
  Named:
    properties:
      name: string
  Person:
    type: Named
    (tier): gold
    properties:
      friends: Person[]
      known: Named[]
  Chain:
    properties:
      next?: Chain | nil
  Bounded:
    type: integer | number
    maximum: 10
/people:
  get:
    queryParameters:
      page: integer
    responses:
      200:
        body:
          application/json:
            type: Person[]
"""

#: Keys whose value is an address rather than data.
REFERENCE_KEYS = frozenset({'$ref', 'declaration', 'id', 'type'})


def references(value: object, key: str = '') -> list[tuple[str, str]]:
    """Every `(key, address)` pair the projection emits, however deep."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name in REFERENCE_KEYS and isinstance(item, str) and item.startswith('pyraml://'):
                found.append((name, item))
            found += references(item, name)
    elif isinstance(value, list):
        for item in value:
            found += references(item, key)
    return found


@pytest.fixture
def both(workspace):
    root = workspace({'api.raml': API})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    return effective(raml), build_graph(raml)


class TestEveryReferenceResolves:
    def test_the_projection_emits_references_at_all(self, both):
        """Guards the guard: a walker that found nothing would pass everything."""
        projection, _ = both
        found = references(projection)
        assert {'id', '$ref'} <= {key for key, _ in found}, sorted({key for key, _ in found})
        assert len(found) > 20, len(found)

    def test_every_address_it_emits_is_a_node_in_the_graph(self, both):
        """The join. Both outputs come from one `Walk`, so an address that
        names nothing means the addressable set is wrong, not that the document
        is.
        """
        projection, graph = both
        dangling = sorted({address for _, address in references(projection) if address not in graph.nodes})
        assert not dangling, f'{len(dangling)} addresses reach no node: {dangling[:5]}'

    def test_a_recursion_head_points_at_the_declaration(self, both):
        """`Chain | nil` is a union whose first member is the recursion marker."""
        projection, graph = both
        union = projection['types']['api.raml']['Chain']['properties']['next']['type']
        head = union['any_of'][0]['head']
        assert head['$ref'] == f'{graph.base}#/declarations/types/Chain'
        assert graph.nodes[head['$ref']].entity.name == 'Chain'

    def test_a_declared_supertype_is_referenced_rather_than_repeated(self, both):
        """`Named`, the string, would be ambiguous across two libraries. The
        declaration is under `types` already, so a `$ref` loses nothing and
        repeating it would make one type read differently depending on which
        subtype you arrived through.
        """
        projection, graph = both
        person = projection['types']['api.raml']['Person']
        assert person['inherits'] == [{'$ref': f'{graph.base}#/declarations/types/Named'}]

    def test_an_anonymous_supertype_is_inlined_rather_than_referenced(self, both):
        """`type: integer | number` with a facet beside it: P9 distributes the
        facet, and each member gains an anonymous parent. It is in no other part
        of the tree, so a reference to it would be the one thing this projection
        must not do — drop data the graph does not carry either.
        """
        projection, _ = both
        member = projection['types']['api.raml']['Bounded']['any_of'][0]
        parent = member['inherits'][0]
        assert '$ref' not in parent, 'an anonymous supertype must not be a bare reference'
        assert parent['kind'] == 'IntegerShape'
        assert parent['id'].endswith('/inherits/anonymous')

    def test_an_alias_resolves_to_the_type_it_aliases(self, both):
        """`Named[]` puts an *alias* of `Named` under `items`, not the
        declaration (docs/07 § 3.6), so the alias is the hop that has to be
        followable. `Person[]` inside `Person` is a cycle instead, and comes out
        as a recursion marker — which is why this case uses the other array.
        """
        projection, graph = both
        known = projection['types']['api.raml']['Person']['properties']['known']['type']
        assert known['items']['alias_of'] == {'$ref': f'{graph.base}#/declarations/types/Named'}

    def test_an_applied_annotation_points_at_its_type(self, both):
        projection, graph = both
        applied = projection['types']['api.raml']['Person']['annotations']
        assert applied == [{'name': 'tier', 'type': f'{graph.base}#/declarations/annotations/tier'}]


class TestTheProjectionAndTheGraphAgree:
    def test_a_declared_type_has_the_same_address_in_both(self, both):
        projection, graph = both
        assert projection['types']['api.raml']['Person']['id'] == graph.find('Person', kinds=['Type'])[0]

    def test_an_operation_has_the_same_address_in_both(self, both):
        projection, graph = both
        operation = projection['endpoints']['/people']['operations']['get']
        assert operation['id'] in graph.nodes
        assert graph.nodes[operation['id']].attributes['method'] == 'get'


class TestATypedFragmentIsADeclaration:
    """A `#%RAML 1.0 DataType` document is one declaration, and no `types:`
    block need mention it. Read only from `fragment_types`, such a document
    projected as having no types at all — silently, because an empty map is
    exactly what a document with no types looks like. The graph carries the same
    branch (docs/16 § 2.9).
    """

    FRAGMENT = '#%RAML 1.0 DataType\ntype: object\nproperties:\n  id: string\n'

    @pytest.fixture
    def entry(self, workspace):
        root = workspace({'user.raml': self.FRAGMENT})
        return parse_from_path(root / 'user.raml', ParseOptions(unwrap=True))

    def test_the_fragment_is_projected_as_a_type(self, entry):
        declared = effective(entry)['types']['user.raml']
        assert list(declared) == ['user.raml']
        assert declared['user.raml']['kind'] == 'ObjectShape'
        assert list(declared['user.raml']['properties']) == ['id']

    def test_it_lands_at_the_address_the_graph_gave_it(self, entry):
        graph = build_graph(entry)
        projected = effective(entry)['types']['user.raml']['user.raml']
        assert projected['id'] == f'{graph.base}#/declarations/types/user.raml'
        assert projected['id'] in graph.nodes

    def test_its_positions_are_projected_too(self, entry):
        assert positions_of(entry)['user.raml']['user.raml']['key'] is not None

    def test_an_included_fragment_is_listed_only_where_it_was_named(self, workspace):
        """Included under a `types:` name it is already there, under that name.

        The graph addresses its shape *under* that declaration —
        `…/types/User/inherits/user.raml` — rather than top-level, so a second
        entry here would invent a declaration the graph does not have.
        """
        root = workspace(
            {
                'user.raml': self.FRAGMENT,
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  User: !include user.raml\n',
            }
        )
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        declared = effective(raml)['types']
        assert {file: list(names) for file, names in declared.items()} == {'api.raml': ['User']}
        assert set(build_graph(raml).nodes) >= {addr for _, addr in references(declared)}


class TestAnAddressMapCanBeReused:
    def test_passing_a_graph_s_map_gives_the_same_projection(self, workspace):
        """The join is only real if the two agree, and they agree because it is
        one map rather than two walks that happen to match.
        """
        root = workspace({'api.raml': API})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)
        assert effective(raml, addresses=graph.addresses) == effective(raml)


DOCUMENTED = """#%RAML 1.0
title: Docs
version: v2
baseUri: https://api.example.test/{tenant}
baseUriParameters:
  tenant:
    description: which tenant
documentation:
  - title: Getting started
    content: Read this first.
securitySchemes:
  oauth:
    type: OAuth 2.0
    describedBy:
      headers:
        Authorization:
          description: bearer token
    settings:
      authorizationUri: https://example.test/auth
      accessTokenUri: https://example.test/token
      authorizationGrants: [authorization_code]
      scopes: [read, write]
annotationTypes:
  deprecated: string
/users:
  displayName: Users collection
  description: the users resource
  (deprecated): use /people
  securedBy: [oauth]
  get:
    (deprecated): use GET /people
    responses:
      200:
        (deprecated): going away
        description: ok
"""


class TestWhatADocumentationViewNeeds:
    """docs/16 § 11.4. A renderer reads this, so what a reader has to see has to
    be in it. Each of these reached no view at all and the omission was
    invisible: an absent key looks exactly like a document that did not say it.
    """

    @pytest.fixture
    def doc(self, workspace):
        root = workspace({'api.raml': DOCUMENTED})
        return effective(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_base_uri_parameters_are_projected(self, doc):
        """`{tenant}` is a value every caller supplies; without it no request
        can be built at all.
        """
        declared = doc['entry_point']['base_uri_parameters']
        assert declared['tenant']['binding'] == 'uri'
        assert declared['tenant']['type']['description'] == 'which tenant'

    def test_documentation_items_are_projected(self, doc):
        assert doc['entry_point']['documentation'] == [{'title': 'Getting started', 'content': 'Read this first.'}]

    def test_a_resource_carries_its_own_prose(self, doc):
        """An operation had `displayName` and `description` and its resource had
        neither, which is what a navigation pane is built from.
        """
        users = doc['endpoints']['/users']
        assert users['display_name'] == 'Users collection'
        assert users['description'] == 'the users resource'

    def test_a_scheme_says_how_to_satisfy_it(self, doc):
        scheme = doc['security_schemes']['api.raml']['oauth']
        assert scheme['type'] == 'OAuth 2.0'
        assert scheme['settings']['authorizationUri'] == 'https://example.test/auth'
        assert scheme['settings']['scopes'] == ['read', 'write']
        assert scheme['settings']['authorizationGrants'] == ['authorization_code']

    def test_a_scheme_says_what_a_request_must_carry(self, doc):
        described = doc['security_schemes']['api.raml']['oauth']['described_by']
        assert described['headers']['Authorization']['type']['description'] == 'bearer token'

    def test_a_secured_by_entry_resolves_into_the_scheme_section(self, doc):
        """The join within one document: `securedBy:` points at the declaration
        by address, and the declaration is now here to be found.
        """
        applied = doc['endpoints']['/users']['secured_by'][0]
        assert applied['declaration'] == doc['security_schemes']['api.raml']['oauth']['id']


class TestAnAnnotationIsRecordedWhereItWasApplied:
    """The document-wide list gives `target: "Resource"` — a *kind*, not an
    address — so a reader could see that something was deprecated and not what.
    """

    @pytest.fixture
    def doc(self, workspace):
        root = workspace({'api.raml': DOCUMENTED})
        return effective(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_on_the_resource(self, doc):
        assert doc['endpoints']['/users']['annotations'] == [
            {'name': 'deprecated', 'type': 'pyraml://id#/declarations/annotations/deprecated'}
        ]

    def test_on_the_operation(self, doc):
        assert [a['name'] for a in doc['endpoints']['/users']['operations']['get']['annotations']] == ['deprecated']

    def test_on_the_response(self, doc):
        response = doc['endpoints']['/users']['operations']['get']['responses']['200']
        assert [a['name'] for a in response['annotations']] == ['deprecated']

    def test_each_points_at_a_type_that_exists(self, workspace):
        root = workspace({'api.raml': DOCUMENTED})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)
        dangling = [a for _, a in references(effective(raml)) if a not in graph.nodes]
        assert not dangling, dangling
