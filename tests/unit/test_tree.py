"""The effective document as an addressed tree (docs/16 § 6).

The projection is pinned whole by the golden layer. What is asserted here is the
property the goldens cannot see: that every reference it emits **resolves**, and
resolves to the node the graph put at the same address.

That is the check on the addressable set. A reference to something the
walk never reached would come out as `null`, which reads exactly like "there was
nothing to point at" — the silent failure the whole addressing scheme exists to
remove.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.types.unwrap import unwrap_shapes
from fastraml.views.graph import build_graph
from fastraml.views.tree import build_tree, positions_of
from tests.unit.conftest import CountingLoader, write_files

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
            if name in REFERENCE_KEYS and isinstance(item, str) and item.startswith('fastraml://'):
                found.append((name, item))
            found += references(item, name)
    elif isinstance(value, list):
        for item in value:
            found += references(item, key)
    return found


@pytest.fixture
def both(workspace):
    root = workspace({'api.raml': API})
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
    return build_tree(raml), build_graph(raml)


class TestWireContract:
    def test_the_effective_view_identifies_its_format(self, both):
        projection, _ = both
        assert projection['format'] == 'fastraml-tree'
        assert projection['format_version'] == 1
        assert projection['view'] == 'effective'
        assert projection['entry_point']['kind'] == 'API'

    def test_protocols_have_one_wire_spelling(self, workspace):
        root = workspace({'api.raml': '#%RAML 1.0\ntitle: t\nprotocols: [hTtPs]\n'})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        assert build_tree(raml)['entry_point']['protocols'] == ['HTTPS']

    def test_fragment_kind_is_not_guessed_from_the_shared_model_class(self, workspace):
        root = workspace({'annotation.raml': '#%RAML 1.0 AnnotationTypeDeclaration\ntype: string\n'})
        raml = workspace.parse(root / 'annotation.raml', ParseOptions(unwrap=True))
        assert build_tree(raml)['entry_point']['kind'] == 'AnnotationTypeDeclaration'


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
        marker = union['any_of'][0]
        assert marker['type'] == 'recursive', 'the cycle must be marked, not left as a bare link'
        assert marker['head'] == {'$ref': f'{graph.base}#/declarations/types/Chain'}
        assert graph.nodes[marker['head']['$ref']].entity.name == 'Chain'

    @pytest.mark.parametrize('declaration', ['/^x/: P', 'x?: P'], ids=['pattern-property', 'property'])
    def test_marking_a_parents_cycle_leaves_the_subtypes_declaration_alone(self, workspace, declaration):
        """docs/07 § 6: `C` inherits `P`'s self-referencing declaration. Marking
        replaces the declaration in `P`'s set rather than editing the one `C`
        shares, so `C`'s still names `P` instead of carrying `P`'s marker.
        """
        body = f'  P:\n    properties:\n      {declaration}\n  C:\n    type: P\n'
        root = workspace({'api.raml': f'#%RAML 1.0\ntitle: t\ntypes:\n{body}'})
        declared = build_tree(workspace.parse(root / 'api.raml', ParseOptions(unwrap=True)))['types']['api.raml']
        field = 'pattern_properties' if declaration.startswith('/') else 'properties'
        [marker] = [entry['type'] for entry in declared['P'][field].values()]
        assert marker['type'] == 'recursive'
        assert marker['head']['$ref'].endswith('#/declarations/types/P')
        [inherited] = [entry['type'] for entry in declared['C'][field].values()]
        assert inherited['$ref'].endswith('#/declarations/types/P')

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
        assert parent['type'] == 'integer'
        assert parent['id'].endswith('/inherits/anonymous')

    def test_an_alias_reads_as_the_type_it_aliases(self, both):
        """`Named[]` puts an *alias* of `Named` under `items` (docs/07 § 3),
        and the alias never reaches the output: `items` is a link to `Named`.

        `Person[]` inside `Person` is a cycle instead and comes out as a
        recursion marker, which is why this case uses the other array.
        """
        projection, graph = both
        known = projection['types']['api.raml']['Person']['properties']['known']['type']
        assert known['items'] == {'$ref': f'{graph.base}#/declarations/types/Named'}

    def test_an_applied_annotation_points_at_its_type(self, both):
        projection, graph = both
        applied = projection['types']['api.raml']['Person']['annotations']
        assert applied == [{'name': 'tier', 'type': f'{graph.base}#/declarations/annotations/tier', 'value': 'gold'}]


#: Declared aliases, local, chained and across a library, each used where the
#: tree writes an address: a property, an array's items, a supertype, a cycle
#: through an alias, and an applied annotation.
ALIASED = {
    'lib.raml': '#%RAML 1.0 Library\ntypes:\n  ID:\n    type: string\n    minLength: 1\n',
    'api.raml': """#%RAML 1.0
title: Aliased
uses:
  generic: lib.raml
annotationTypes:
  base: string
  tag: base
types:
  ID: generic.ID
  Key: string
  Code: Key
  Again: Code
  Link:
    (tag): x
    properties:
      id: ID
      codes: Again[]
      next?: Chain
  Chain: Link
/things/{id}:
  uriParameters:
    id:
      type: ID
""",
}


class TestADeclaredAliasIsADeclaration:
    """`ID: Key` is a second declaration identity for one type (docs/07 § 3),
    and after P9 it carries the referent's effective facets. Every reference to
    `ID` is to `ID`'s address, so the tree must hold a node there; only an
    anonymous alias is transparent (docs/16 § 6.1).

    Resolution is checked against the tree's own `id`s, which is what a consumer
    resolves by. `graph.nodes` has the alias either way.
    """

    @pytest.fixture
    def doc(self, workspace):
        root = workspace(ALIASED)
        return build_tree(workspace.parse(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_every_link_names_a_node_the_tree_carries(self, doc):
        found = references(doc)
        ids = {address for key, address in found if key == 'id'}
        links = [address for key, address in found if key != 'id']
        assert links, 'guards the guard'
        # `references` reads addresses only, and a link with none is the worst
        # case: `{"$ref": null}` is a head the walk never addressed.
        assert '"$ref": null' not in json.dumps(doc), 'a link with no address'
        dangling = [address for address in links if address not in ids]
        assert not dangling, dangling

    def test_it_is_emitted_under_its_own_name_with_the_effective_facets(self, doc):
        declared = doc['types']['api.raml']['ID']
        assert declared['id'] == 'fastraml://id#/declarations/types/ID'
        assert (declared['name'], declared['type'], declared['type_expr']) == ('ID', 'string', 'generic.ID')
        assert declared['min_length'] == 1

    def test_it_links_the_type_it_is_a_second_name_for(self, doc):
        assert doc['types']['api.raml']['ID']['alias'] == {'$ref': doc['types']['lib.raml']['ID']['id']}
        tag = doc['annotation_types']['api.raml']['tag']
        assert tag['alias'] == {'$ref': doc['annotation_types']['api.raml']['base']['id']}

    def test_a_chain_links_one_step_at_a_time(self, doc):
        declared = doc['types']['api.raml']
        assert declared['Again']['alias'] == {'$ref': declared['Code']['id']}
        assert declared['Code']['alias'] == {'$ref': declared['Key']['id']}

    def test_only_an_alias_carries_the_key(self, doc):
        declared = doc['types']['api.raml']
        assert 'alias' not in declared['Key']
        assert 'alias' not in declared['Link']['properties']['id']['type'], 'an anonymous alias is the link itself'

    def test_a_use_site_links_to_the_alias_not_its_referent(self, doc):
        own = doc['types']['api.raml']['ID']['id']
        assert doc['types']['api.raml']['Link']['properties']['id']['type'] == {'$ref': own}
        assert doc['endpoints']['/things/{id}']['uri_parameters']['id']['type']['inherits'] == [{'$ref': own}]

    def test_each_link_in_a_chain_is_its_own_declaration(self, doc):
        declared = doc['types']['api.raml']
        assert declared['Link']['properties']['codes']['type']['items'] == {'$ref': declared['Again']['id']}
        assert [declared[name]['type_expr'] for name in ('Again', 'Code')] == ['Code', 'Key']

    def test_an_alias_of_a_recursive_type_shares_its_marked_cycle(self, doc):
        # The two share one `properties` container (docs/07 § 3), so one marker
        # serves both, headed by the declaration the walk was inside.
        declared = doc['types']['api.raml']
        for name in ('Link', 'Chain'):
            marker = declared[name]['properties']['next']['type']
            assert marker['type'] == 'recursive'
            assert marker['head'] == {'$ref': declared['Link']['id']}

    def test_an_annotation_bound_through_an_alias_points_at_the_alias(self, doc):
        tag = doc['annotation_types']['api.raml']['tag']
        assert (tag['name'], tag['type_expr']) == ('tag', 'base')
        assert doc['types']['api.raml']['Link']['annotations'][0]['type'] == tag['id']


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
    branch (docs/16 § 6.1).
    """

    FRAGMENT = '#%RAML 1.0 DataType\ntype: object\nproperties:\n  id: string\n'

    @pytest.fixture
    def entry(self, workspace):
        root = workspace({'user.raml': self.FRAGMENT})
        return workspace.parse(root / 'user.raml', ParseOptions(unwrap=True))

    def test_the_fragment_is_projected_as_a_type(self, entry):
        declared = build_tree(entry)['types']['user.raml']
        assert list(declared) == ['user.raml']
        assert declared['user.raml']['type'] == 'object'
        assert list(declared['user.raml']['properties']) == ['id']

    def test_it_lands_at_the_address_the_graph_gave_it(self, entry):
        graph = build_graph(entry)
        projected = build_tree(entry)['types']['user.raml']['user.raml']
        assert projected['id'] == f'{graph.base}#/declarations/types/user.raml'
        assert projected['id'] in graph.nodes

    def test_its_positions_are_projected_too(self, entry):
        assert positions_of(entry)['user.raml']['user.raml']['key'] is not None

    def test_an_included_fragment_has_its_own_canonical_declaration(self, workspace):
        root = workspace(
            {
                'user.raml': self.FRAGMENT,
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  User: !include user.raml\n',
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        declared = build_tree(raml)['types']
        assert {file: list(names) for file, names in declared.items()} == {
            'api.raml': ['User'],
            'user.raml': ['user.raml'],
        }
        fragment = declared['user.raml']['user.raml']
        assert fragment['id'] == 'fastraml://id/user.raml#/declarations/types/user.raml'
        assert declared['api.raml']['User']['inherits'] == [{'$ref': fragment['id']}]
        assert set(build_graph(raml).nodes) >= {addr for _, addr in references(declared)}

    @pytest.mark.parametrize('order', [('A', 'B'), ('B', 'A')])
    def test_repeated_inclusions_share_a_root_independent_of_visit_order(self, workspace, order):
        definitions = {
            'A': '  A: !include ./models/user.raml\n',
            'B': '  B:\n    type: !include /models/user.raml\n    properties:\n      extra?: string\n',
        }
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n' + ''.join(definitions[name] for name in order),
                'models/user.raml': self.FRAGMENT,
            }
        )
        loader = CountingLoader(root, workspace)
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True, file_loader=loader))
        tree = build_tree(raml)
        fragment = tree['types']['models/user.raml']['user.raml']
        expected = 'fastraml://id/models%2Fuser.raml#/declarations/types/user.raml'
        assert fragment['id'] == expected
        assert fragment['name'] == 'user.raml'
        declared = tree['types']['api.raml']
        assert list(declared) == list(order)
        assert declared['A']['inherits'] == declared['B']['inherits'] == [{'$ref': expected}]
        assert list(declared['A']['properties']) == list(fragment['properties']) == ['id']
        assert set(declared['B']['properties']) == {'id', 'extra'}
        a, b = raml.entry_point.types['A'], raml.entry_point.types['B']
        assert a.inherits[0] is b.inherits[0]
        assert loader.counts[(root / 'models' / 'user.raml').as_uri()] == 1
        assert 'models/user.raml' in positions_of(raml)
        graph = build_graph(raml)
        assert graph.nodes[expected].entity is a.inherits[0]
        assert {edge.subject for edge in graph.into(expected, ['inherits'])} == {
            declared['A']['id'],
            declared['B']['id'],
        }

    def test_a_body_only_include_is_a_referenceable_type(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\n/users:\n  get:\n    responses:\n      200:\n'
                '        body:\n          application/json:\n            type: !include user.raml\n',
                'user.raml': self.FRAGMENT,
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        tree = build_tree(raml)
        fragment = tree['types']['user.raml']['user.raml']
        body = tree['endpoints']['/users']['operations']['get']['responses']['200']['bodies']['application/json']
        assert body['inherits'] == [{'$ref': fragment['id']}]
        assert list(body['properties']) == ['id']
        assert fragment['id'] in build_graph(raml).nodes

    def test_same_basename_fragments_are_qualified_by_workspace_path(self, workspace):
        root = workspace(
            {
                'apis/api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n'
                '  A: !include ../models/user.raml\n  B: !include ../external/user.raml\n',
                'models/user.raml': self.FRAGMENT,
                'external/user.raml': '#%RAML 1.0 DataType\ntype: string\n',
            }
        )
        raml = workspace.parse(root / 'apis' / 'api.raml', ParseOptions(unwrap=True, workspace_root=root))
        tree = build_tree(raml)
        first = tree['types']['models/user.raml']['user.raml']
        second = tree['types']['external/user.raml']['user.raml']
        assert first['name'] == second['name'] == 'user.raml'
        assert first['id'] != second['id']
        assert first['type'] == 'object'
        assert second['type'] == 'string'
        assert tree['types']['apis/api.raml']['A']['inherits'] == [{'$ref': first['id']}]
        assert tree['types']['apis/api.raml']['B']['inherits'] == [{'$ref': second['id']}]

    def test_remote_fragment_identity_keeps_the_full_url_and_query(self, workspace):
        urls = [
            'https://one.example/types/user.raml?v=1',
            'https://two.example/types/user.raml?v=1',
            'https://one.example/types/user.raml?v=2',
        ]

        class Client:
            def get(self, url):
                assert url in urls
                return SimpleNamespace(status_code=200, content=TestATypedFragmentIsADeclaration.FRAGMENT.encode())

        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n'
                + ''.join(f'  T{index}: !include {url}\n' for index, url in enumerate(urls))
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, http_client=Client()))
        tree = build_tree(raml)
        addresses = []
        for index, url in enumerate(urls):
            fragment = tree['types'][url]['user.raml']
            expected = f'fastraml://id/{quote(url, safe="")}#/declarations/types/user.raml'
            assert fragment['name'] == 'user.raml'
            assert fragment['id'] == expected
            assert tree['types']['api.raml'][f'T{index}']['inherits'] == [{'$ref': expected}]
            assert url in positions_of(raml)
            addresses.append(expected)
        assert len(set(addresses)) == len(urls)
        assert set(addresses) <= set(build_graph(raml).nodes)

    @pytest.mark.parametrize('scheme', ['http', 'https'])
    def test_remote_fragments_keep_full_urls_with_a_remote_workspace_root(self, scheme):
        from fastraml.loaders import HTTPLoader
        from fastraml.parser.fragments import FragmentKind, decode_fragment
        from fastraml.registry import Raml
        from fastraml.types.resolve import resolve_shapes

        urls = [f'{scheme}://one.example/types/user.raml', f'{scheme}://two.example/types/user.raml']

        class Client:
            def get(self, url):
                assert url in urls
                return SimpleNamespace(status_code=200, content=TestATypedFragmentIsADeclaration.FRAGMENT.encode())

        raml = Raml(loader=HTTPLoader(Client()), workspace_root_uri=f'{scheme}://one.example/types/')
        entry_uri = f'{scheme}://one.example/api.raml'
        source = '#%RAML 1.0\ntitle: T\ntypes:\n' + ''.join(
            f'  T{index}: !include {url}\n' for index, url in enumerate(urls)
        )
        raml.entry_point = decode_fragment(raml, entry_uri, FragmentKind.API, source)
        resolve_shapes(raml)
        unwrap_shapes(raml)
        tree = build_tree(raml)
        for index, url in enumerate(urls):
            fragment = tree['types'][url]['user.raml']
            assert fragment['id'] == f'fastraml://id/{quote(url, safe="")}#/declarations/types/user.raml'
            assert tree['types'][raml.location][f'T{index}']['inherits'] == [{'$ref': fragment['id']}]
            assert url in positions_of(raml)
        assert {tree['types'][url]['user.raml']['id'] for url in urls} <= set(build_graph(raml).nodes)

    @pytest.mark.parametrize('entry_only', [False, True])
    def test_annotation_fragments_use_the_annotation_inventory_and_address(self, workspace, entry_only):
        root = workspace(
            {
                'annotation.raml': '#%RAML 1.0 AnnotationTypeDeclaration\ntype: string\nallowedTargets: TypeDeclaration\n',
                'api.raml': '#%RAML 1.0\ntitle: T\nannotationTypes:\n  ann: !include annotation.raml\n'
                'types:\n  T:\n    type: string\n    (ann): x\n',
            }
        )
        raml = workspace.parse(root / ('annotation.raml' if entry_only else 'api.raml'), ParseOptions(unwrap=True))
        tree = build_tree(raml)
        fragment = tree['annotation_types']['annotation.raml']['annotation.raml']
        unit = 'fastraml://id' if entry_only else 'fastraml://id/annotation.raml'
        assert fragment['id'] == f'{unit}#/declarations/annotations/annotation.raml'
        assert 'annotation.raml' not in tree['types']
        assert fragment['id'] in build_graph(raml).nodes
        assert positions_of(raml)['annotation.raml']['annotation.raml']['key'] is not None
        if not entry_only:
            assert tree['annotation_types']['api.raml']['ann']['inherits'] == [{'$ref': fragment['id']}]

    def test_headerless_includes_keep_their_includers_namespace(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\nuses:\n  a: a.raml\n  b: b.raml\n',
                'a.raml': '#%RAML 1.0 Library\ntypes:\n  Local: string\n  T: !include content.yaml\n',
                'b.raml': '#%RAML 1.0 Library\ntypes:\n  Local: integer\n  T: !include content.yaml\n',
                'content.yaml': 'type: Local\n',
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        tree = build_tree(raml)
        assert 'content.yaml' not in tree['types']
        first = tree['types']['a.raml']['T']['inherits'][0]
        second = tree['types']['b.raml']['T']['inherits'][0]
        assert '$ref' not in first
        assert '$ref' not in second
        assert first['id'] != second['id']
        assert first['type'] == 'string'
        assert second['type'] == 'integer'

    def test_a_fragment_inclusion_chain_links_each_canonical_root(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  User: !include outer.raml\n',
                'outer.raml': '#%RAML 1.0 DataType\ntype: !include user.raml\n',
                'user.raml': self.FRAGMENT,
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        tree = build_tree(raml)
        outer = tree['types']['outer.raml']['outer.raml']
        inner = tree['types']['user.raml']['user.raml']
        assert tree['types']['api.raml']['User']['inherits'] == [{'$ref': outer['id']}]
        assert outer['inherits'] == [{'$ref': inner['id']}]
        assert list(outer['properties']) == list(inner['properties']) == ['id']

    def test_external_json_schema_roots_are_canonical_too(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  A: !include user.json\n  B: !include user.json\n',
                'user.json': '{"type": "string", "minLength": 2}',
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        tree = build_tree(raml)
        fragment = tree['types']['user.json']['user.json']
        assert fragment['id'] == 'fastraml://id/user.json#/declarations/types/user.json'
        assert fragment['projection']['min_length'] == 2
        for name in ['A', 'B']:
            assert tree['types']['api.raml'][name]['inherits'] == [{'$ref': fragment['id']}]

    def test_recursive_fragment_content_keeps_a_resolvable_recursion_head(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  Node: !include node.raml\n',
                'node.raml': '#%RAML 1.0 DataType\ntype: object\nproperties:\n  next?:\n    type: !include node.raml\n',
            }
        )
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        tree = build_tree(raml)
        fragment = tree['types']['node.raml']['node.raml']
        assert tree['types']['api.raml']['Node']['inherits'] == [{'$ref': fragment['id']}]
        marker = fragment['properties']['next']['type']['properties']['next']['type']
        assert marker['type'] == 'recursive'
        ids = {address for key, address in references(tree) if key == 'id'}
        assert marker['head']['$ref'] in ids
        assert {address for key, address in references(tree) if key == '$ref'} <= ids

    @pytest.mark.parametrize('entry_only', [False, True])
    @pytest.mark.parametrize('kind', ['DataType', 'AnnotationTypeDeclaration'])
    def test_a_collapsed_fragment_root_is_the_effective_declaration(self, workspace, entry_only, kind):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  A: !include user.raml\n  B: !include user.raml\n',
                'user.raml': f'#%RAML 1.0 {kind}\nuses:\n  l: lib.raml\ntype: [object, l.Text | l.Number]\n'
                'properties:\n  id: string\n',
                'lib.raml': '#%RAML 1.0 Library\ntypes:\n  Text:\n    properties:\n      id: string\n'
                '  Number:\n    properties:\n      id: integer\n',
            }
        )
        raml = workspace.parse(root / ('user.raml' if entry_only else 'api.raml'))
        owner = raml.fragments[(root / 'user.raml').as_uri()]
        original = owner.shape
        unwrap_shapes(raml)
        assert owner.shape is not original, 'the union must collapse to a replacement root'
        tree = build_tree(raml)
        inventory = 'annotation_types' if kind == 'AnnotationTypeDeclaration' else 'types'
        segment = 'annotations' if kind == 'AnnotationTypeDeclaration' else 'types'
        fragment = tree[inventory]['user.raml']['user.raml']
        unit = 'fastraml://id' if entry_only else 'fastraml://id/user.raml'
        assert fragment['id'] == f'{unit}#/declarations/{segment}/user.raml'
        if not entry_only:
            for name in ['A', 'B']:
                assert tree['types']['api.raml'][name]['inherits'] == [{'$ref': fragment['id']}]
                assert raml.entry_point.types[name].inherits[0] is owner.shape
        assert build_graph(raml).nodes[fragment['id']].entity is owner.shape
        assert fragment['type'] == 'object'
        assert list(fragment['properties']) == ['id']


class TestAnAddressMapCanBeReused:
    def test_passing_a_graph_s_map_gives_the_same_projection(self, workspace):
        """The join is only real if the two agree, and they agree because it is
        one map rather than two walks that happen to match.
        """
        root = workspace({'api.raml': API})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)
        assert build_tree(raml, addresses=graph.addresses) == build_tree(raml)


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
    """docs/16 § 6.2. A renderer reads this, so what a reader has to see has to
    be in it. Each of these reached no view at all and the omission was
    invisible: an absent key looks exactly like a document that did not say it.
    """

    @pytest.fixture
    def doc(self, workspace):
        root = workspace({'api.raml': DOCUMENTED})
        return build_tree(workspace.parse(root / 'api.raml', ParseOptions(unwrap=True)))

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
        return build_tree(workspace.parse(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_on_the_resource(self, doc):
        assert doc['endpoints']['/users']['annotations'] == [
            {
                'name': 'deprecated',
                'type': 'fastraml://id#/declarations/annotations/deprecated',
                'value': 'use /people',
            }
        ]

    def test_on_the_operation(self, doc):
        assert [a['name'] for a in doc['endpoints']['/users']['operations']['get']['annotations']] == ['deprecated']

    def test_on_the_response(self, doc):
        response = doc['endpoints']['/users']['operations']['get']['responses']['200']
        assert [a['name'] for a in response['annotations']] == ['deprecated']

    def test_each_site_carries_what_the_annotation_says(self, doc):
        # The document-wide list keys by *kind*, so three `deprecated` entries
        # with three different messages are three rows a reader cannot tell
        # apart. Without the value here a view says a thing is deprecated and
        # not what to use instead.
        operation = doc['endpoints']['/users']['operations']['get']
        assert doc['endpoints']['/users']['annotations'][0]['value'] == 'use /people'
        assert operation['annotations'][0]['value'] == 'use GET /people'
        assert operation['responses']['200']['annotations'][0]['value'] == 'going away'

    def test_each_points_at_a_type_that_exists(self, workspace):
        root = workspace({'api.raml': DOCUMENTED})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        graph = build_graph(raml)
        dangling = [a for _, a in references(build_tree(raml)) if a not in graph.nodes]
        assert not dangling, dangling


NUMBERS = """#%RAML 1.0
title: Numbers
types:
  Limits:
    properties:
      atInt64:
        type: integer
        minimum: -9223372036854775808
        maximum: 9223372036854775807
      atFloat64:
        type: number
        minimum: 2.2250738585072014e-308
        maximum: 1.7976931348623157e308
        multipleOf: 0.1
      ordinary:
        type: number
        minimum: 0
        maximum: 100
        multipleOf: 0.01
      counted:
        type: string
        minLength: 1
        maxLength: 200
"""


class TestABoundSurvivesTheTripToAConsumer:
    """docs/16 § 6.2: a bound is an exact decimal string, on every kind.

    Both halves matter and neither is the other. **Exact**, because JSON's
    number is a double in every consumer that matters, so `9223372036854775807`
    written as one comes back as ...808 — the parser refuses to pass a number
    through `float` and then handed it to one at the last step. **Decimal**,
    because the ratio form that was exact was also unreadable: an integer-valued
    float has an integer ratio, so `1.7976931348623157e308` reached the tree as
    309 digits, 292 of them zeros nobody wrote.
    """

    @pytest.fixture
    def limits(self, workspace):
        root = workspace({'api.raml': NUMBERS})
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        return build_tree(raml)['types']['api.raml']['Limits']['properties']

    def bound(self, limits, name, facet):
        return limits[name]['type'][facet]

    def test_an_integer_bound_past_a_double_keeps_every_digit(self, limits):
        assert self.bound(limits, 'atInt64', 'maximum') == '9223372036854775807'
        assert self.bound(limits, 'atInt64', 'minimum') == '-9223372036854775808'

    def test_a_float_bound_is_written_the_way_its_author_wrote_it(self, limits):
        assert self.bound(limits, 'atFloat64', 'maximum') == '1.7976931348623157E+308'
        assert self.bound(limits, 'atFloat64', 'minimum') == '2.2250738585072014E-308'

    def test_an_ordinary_bound_is_a_plain_decimal(self, limits):
        # Not `1E+2`. Scientific notation is for the case where the plain form
        # is unreadable, and `100` is not that case.
        assert self.bound(limits, 'ordinary', 'maximum') == '100'
        assert self.bound(limits, 'ordinary', 'minimum') == '0'
        assert self.bound(limits, 'atFloat64', 'multiple_of') == '0.1'
        assert self.bound(limits, 'ordinary', 'multiple_of') == '0.01'

    def test_a_count_stays_a_number(self, limits):
        # The distinction is what the facet *means*, not how big it is: a count
        # is bounded by what fits in memory, so a consumer never has to ask
        # which form arrived this time.
        assert self.bound(limits, 'counted', 'min_length') == 1
        assert self.bound(limits, 'counted', 'max_length') == 200


#: An API whose shared library sits beside it rather than beneath it, which is
#: what a project with more than one API does. Reachable only with a workspace
#: root wide enough to hold both, which is what `--workspace-root` is for.
SHARED = {
    'apis/store/api.raml': """#%RAML 1.0
title: Store
uses:
  shared: !include ../../shared/money.raml
types:
  Order:
    properties:
      total: shared.Money
/orders:
  get:
    responses:
      200:
        body:
          application/json:
            type: Order
""",
    'shared/money.raml': """#%RAML 1.0 Library
types:
  Money:
    properties:
      amount: number
""",
}


class TestAPathIsRelativeToTheWorkspaceRoot:
    """docs/16 § 2: no absolute filesystem path enters a view.

    The rule held for a library *beneath* the entry document and nowhere else,
    because it was a prefix strip against the entry's own directory. A sibling
    shares no prefix with it, so every declaration in the shared library kept
    the whole `file:///C:/…/shared/money.raml` — as its key in `types`, and
    inside the IRI of everything it declared. A consumer building a URL out of
    either put the producing machine's filesystem in an address bar.

    The workspace root is the right anchor and not merely a wider one: it is the
    boundary `SafeFileLoader` enforces, so every file a parse can read is at or
    beneath it and no path a view prints ever has to ascend.
    """

    @pytest.fixture
    def tree(self, workspace):
        root = workspace(SHARED)
        raml = workspace.parse(root / 'apis' / 'store' / 'api.raml', ParseOptions(unwrap=True, workspace_root=root))
        return build_tree(raml)

    def test_the_library_is_keyed_by_its_path_under_the_root(self, tree):
        assert sorted(tree['types']) == ['apis/store/api.raml', 'shared/money.raml']

    def test_no_address_carries_a_filesystem_path(self, tree):
        leaked = [address for _, address in references(tree) if 'file%3A' in address or 'file:' in address]
        assert not leaked, leaked

    def test_the_reference_into_the_library_resolves(self, tree):
        raml = tree['types']['apis/store/api.raml']['Order']['properties']['total']['type']
        declared = tree['types']['shared/money.raml']['Money']
        assert raml['$ref'] == declared['id']

    def test_a_relative_workspace_root_is_resolved_before_it_is_named(self, tmp_path, monkeypatch):
        # `path_to_file_uri` has nothing to resolve a relative path against, so
        # `-w apis` named `file:///apis` while the loader confined reads to the
        # absolute one. The two disagreed and only the loader was right.
        root = write_files(tmp_path, SHARED)
        monkeypatch.chdir(root)
        raml = parse_from_path('apis/store/api.raml', ParseOptions(unwrap=True, workspace_root='.'))
        assert list(build_tree(raml)['types']) == ['apis/store/api.raml', 'shared/money.raml']


SCHEMA_API = {
    'api.raml': """#%RAML 1.0
title: Schemas
types:
  Invoice:
    type: !include invoice.json
""",
    'invoice.json': """{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "line": { "$ref": "#/definitions/line" },
    "paid": { "$ref": "money.json#/definitions/Amount" },
    "also": { "$ref": "money.json#/definitions/Amount" }
  },
  "definitions": {
    "line": { "type": "string" },
    "Amount": { "type": "string", "description": "The invoice's own, unrelated." },
    "Currency": { "$ref": "money.json#/definitions/Currency" }
  }
}""",
    'money.json': """{
  "definitions": {
    "Amount": {
      "type": "object",
      "properties": {
        "minor": { "type": "integer" },
        "of": { "$ref": "#/definitions/Amount" },
        "invoice": { "$ref": "invoice.json" },
        "line": { "$ref": "invoice.json#/definitions/line" }
      }
    },
    "Currency": { "type": "string", "enum": ["GBP", "USD"] }
  }
}""",
}


class TestASchemaArrivesSelfContained:
    """docs/16 § 6: `json_schema` is the resolved document.

    A `$ref` naming another file names nothing a reader of the tree has, so a
    schema carrying one describes a type only to someone holding the directory
    it was written in.
    """

    @pytest.fixture
    def schema(self, workspace):
        root = workspace(SCHEMA_API)
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True))
        return build_tree(raml)['types']['api.raml']['Invoice']['json_schema']

    def test_it_is_a_json_value_and_not_a_string(self, schema):
        # A consumer showing it should not have to parse a document the parser
        # has already parsed.
        assert isinstance(schema, dict)

    def test_a_reference_out_of_the_document_is_pulled_in(self, schema):
        assert schema['properties']['paid'] == {'$ref': '#/definitions/Amount2'}
        assert schema['definitions']['Amount2']['properties']['minor'] == {'type': 'integer'}

    def test_a_pointer_within_the_document_stays_a_pointer(self, schema):
        # Followable where it stands, and inlining it loses the sharing the
        # author expressed.
        assert schema['properties']['line'] == {'$ref': '#/definitions/line'}
        assert schema['definitions']['line'] == {'type': 'string'}

    def test_one_target_named_twice_is_pulled_in_once(self, schema):
        assert schema['properties']['also'] == schema['properties']['paid']
        assert sorted(schema['definitions']) == ['Amount', 'Amount2', 'Currency', 'line']

    def test_an_external_definition_alias_is_expanded_in_its_existing_slot(self, schema):
        assert schema['definitions']['Currency'] == {'type': 'string', 'enum': ['GBP', 'USD']}
        assert 'Currency2' not in schema['definitions']

    def test_a_name_the_document_already_uses_is_not_taken(self, schema):
        # The invoice has an `Amount` of its own, and it is not the one being
        # pulled in. A bundle that overwrote it would change what the schema
        # accepts.
        assert schema['definitions']['Amount'] == {'type': 'string', 'description': "The invoice's own, unrelated."}

    def test_a_reference_inside_what_was_pulled_in_resolves_too(self, schema):
        # `money.json`'s own `#/definitions/Amount` is local to *that* file, so
        # it has to be rewritten against where the subschema now lives.
        assert schema['definitions']['Amount2']['properties']['of'] == {'$ref': '#/definitions/Amount2'}

    def test_a_reference_back_into_the_bundled_file_points_into_it(self, schema):
        # docs/10 § 7: the file is one document per parse, so `money.json`'s
        # way back into it lands on the bundle's own root and its own
        # `line`, not on copies pulled in beside them.
        back = schema['definitions']['Amount2']['properties']
        assert back['invoice'] == {'$ref': '#'}
        assert back['line'] == {'$ref': '#/definitions/line'}

    def test_nothing_names_a_file(self, schema):
        text = json.dumps(schema)
        assert 'money.json' not in text


INCLUDED_SCHEME = {
    'api.raml': """#%RAML 1.0
title: Schemes
securitySchemes:
  inline:
    type: OAuth 2.0
    description: Declared in place.
    settings:
      authorizationUri: https://example.com/authorize
      accessTokenUri: https://example.com/token
      authorizationGrants: [authorization_code]
      scopes: [read, write]
  included: !include scheme.raml
/things:
  get:
    securedBy: [included]
""",
    'scheme.raml': """#%RAML 1.0 SecurityScheme
type: OAuth 2.0
description: Declared in a file of its own.
describedBy:
  headers:
    Authorization:
      description: Bearer token
settings:
  authorizationUri: https://example.com/authorize
  accessTokenUri: https://example.com/token
  authorizationGrants: [authorization_code]
  scopes: [read, write]
""",
}


class TestAnIncludedSchemeSaysWhatItIs:
    """docs/16 § 6.2: a scheme is projected through the link it holds.

    `included: !include scheme.raml` decodes to a definition carrying a link and
    nothing else, and the SecurityScheme fragment it points at is one scheme
    rather than a `securitySchemes:` map — so the section built from the
    fragments never reaches it. Read directly, the scheme arrives with an empty
    `type` and no settings while every use site reports it bound, because P5
    applies what `resolved()` gives. A reader is told a request must be
    authenticated and nothing about how.
    """

    @pytest.fixture
    def schemes(self, workspace):
        root = workspace(INCLUDED_SCHEME)
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True, workspace_root=root))
        return build_tree(raml)['security_schemes']['api.raml']

    def test_the_type_is_the_one_the_fragment_declares(self, schemes):
        assert schemes['included']['type'] == 'OAuth 2.0'

    def test_the_settings_are_the_ones_the_fragment_declares(self, schemes):
        assert schemes['included']['settings']['scopes'] == ['read', 'write']

    def test_the_description_is_the_one_the_fragment_declares(self, schemes):
        assert schemes['included']['description'] == 'Declared in a file of its own.'

    def test_described_by_arrives_too(self, schemes):
        assert 'Authorization' in schemes['included']['described_by']['headers']

    def test_it_is_named_for_the_declaration_and_not_for_the_file(self, schemes):
        # `securedBy:` writes `included`; the link target is named `scheme.raml`.
        assert schemes['included']['name'] == 'included'

    def test_it_says_what_an_equivalent_inline_declaration_says(self, schemes):
        said = {name: dict(scheme) for name, scheme in schemes.items()}
        for scheme in said.values():
            del scheme['id'], scheme['name'], scheme['description']
            scheme.pop('described_by', None)
        assert said['included'] == said['inline']

    def test_the_use_site_points_at_the_declaration(self, workspace):
        root = workspace(INCLUDED_SCHEME)
        raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, validate=True, workspace_root=root))
        tree = build_tree(raml)
        secured = tree['endpoints']['/things']['operations']['get']['secured_by'][0]
        assert secured['declaration'] == tree['security_schemes']['api.raml']['included']['id']


METADATA = {
    'api.raml': """#%RAML 1.0
title: Metadata
annotationTypes:
  note: string
types:
  Nameable:
    type: object
    facets:
      onlyIn: string
      since?: integer
      internal?: boolean
  Money:
    type: Nameable
    onlyIn: EU
    properties:
      amount: number
    examples:
      typical:
        displayName: A typical amount
        description: What most callers send.
        (note): shown first
        value:
          amount: 3.5
      broken:
        strict: false
        value:
          amount: not a number
  Single:
    type: number
    example:
      displayName: The canonical one
      value: 1
""",
}


@pytest.fixture
def metadata_tree(workspace):
    root = workspace(METADATA)
    raml = workspace.parse(root / 'api.raml', ParseOptions(unwrap=True, workspace_root=root))
    return build_tree(raml)


@pytest.fixture
def metadata(metadata_tree):
    return metadata_tree['types']['api.raml']


@pytest.fixture
def annotation_types(metadata_tree):
    return metadata_tree['annotation_types']['api.raml']


class TestAnExampleCarriesWhatWasWrittenBesideIt:
    """docs/16 § 6.2: form B's metadata is data, and it was being dropped.

    `displayName`, `description`, `strict` and an example's own annotations all
    reached the model and none reached a consumer — silently, since an example
    with no metadata and one whose metadata was discarded projected as the same
    bare value.
    """

    def test_the_value_is_under_value_on_every_example(self, metadata):
        examples = metadata['Money']['examples']
        assert examples['typical']['value'] == {'amount': 3.5}

    def test_the_singular_facet_has_the_same_shape_as_a_named_one(self, metadata):
        # One form, always. A consumer that had to test which arrived is what
        # § 11.4a exists to prevent.
        assert metadata['Single']['example'] == {'value': 1, 'display_name': 'The canonical one'}

    def test_a_display_name_and_a_description_survive(self, metadata):
        typical = metadata['Money']['examples']['typical']
        assert typical['display_name'] == 'A typical amount'
        assert typical['description'] == 'What most callers send.'

    def test_strict_false_survives(self, metadata):
        # The reason that example is in the document: it deliberately does not
        # validate. Dropped, it reads as an example that does.
        assert metadata['Money']['examples']['broken']['strict'] is False

    def test_an_annotation_on_an_example_points_at_its_type(self, metadata, annotation_types):
        applied = metadata['Money']['examples']['typical']['annotations']
        assert [one['name'] for one in applied] == ['note']
        assert applied[0]['value'] == 'shown first'
        assert applied[0]['type'] == annotation_types['note']['id']

    def test_an_example_with_no_metadata_carries_only_its_value(self, metadata):
        assert set(metadata['Money']['examples']['broken']) == {'value', 'strict'}


class TestADeclaredFacetSaysWhatASubtypeMustSupply:
    """docs/10 § 4: a `facets:` block declares what *subtypes* must supply.

    The names alone were what this projected, so a consumer could say that
    `Nameable` demands `onlyIn` and not that it demands a string, nor that
    `since?` is optional — and the list was sorted, which the model's
    declaration-order invariant does not allow.
    """

    def test_each_facet_carries_its_type(self, metadata):
        declared = metadata['Nameable']['declared_facets']
        assert declared['onlyIn']['type']['type'] == 'string'
        assert declared['since']['type']['type'] == 'integer'

    def test_an_optional_facet_is_distinguishable_from_a_required_one(self, metadata):
        declared = metadata['Nameable']['declared_facets']
        assert declared['onlyIn']['required'] is True
        assert declared['since']['required'] is False

    def test_declaration_order_is_preserved(self, metadata):
        # Not alphabetical: `internal` sorts first and is written last.
        assert list(metadata['Nameable']['declared_facets']) == ['onlyIn', 'since', 'internal']

    def test_the_value_a_subtype_supplies_stays_in_custom_facets(self, metadata):
        # The two halves are separate keys: what is demanded, and what is given.
        assert metadata['Money']['custom_facets'] == {'onlyIn': 'EU'}
        assert 'declared_facets' not in metadata['Money']
