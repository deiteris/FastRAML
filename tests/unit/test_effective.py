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
from pyraml.effective import effective
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
        if key == 'inherits':
            found += [(key, item) for item in value if isinstance(item, str)]
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
