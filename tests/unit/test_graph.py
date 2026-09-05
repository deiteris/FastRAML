"""The graph projection — docs/16-graph.md.

Three things are worth pinning here and nothing else is:

- **the IRI scheme**, because every consumer, diff and cached query result
  depends on an IRI meaning the same thing twice (§ 3);
- **the edges that answer the questions the projection exists for**, walked in
  both directions, because a projection that builds without error and links
  nothing still builds without error;
- **the serialisations, against a real RDF parser**, because a hand-written
  emitter that produces almost-valid N-Triples is the obvious failure mode and
  eyeballing it does not catch a missing dot.

What is deliberately not here: a golden of the whole graph. `tests/golden`
already pins the model this reads, and a second whole-model snapshot would
churn on every fixture edit while asserting the same facts twice.
"""

from __future__ import annotations

import io
import json

import pytest

import pyraml.graph as graph_module
from pyraml import ParseOptions, parse_from_path
from pyraml.graph import (
    DEFAULT_BASE,
    RAML_NS,
    TYPE_EDGES,
    USE_EDGES,
    Graph,
    build_graph,
)

API = """#%RAML 1.0
title: Demo
uses:
  lib: lib.raml
types:
  Wrapper:
    type: object
    properties:
      inner: lib.Address
/users:
  /{userId}:
    get:
      queryParameters:
        limit?:
          type: integer
          maximum: 100
      responses:
        200:
          body:
            application/json:
              type: lib.UserList
"""

LIB = """#%RAML 1.0 Library
types:
  Entity:
    type: object
    properties:
      id: string
  Address:
    type: object
    properties:
      city: string
  User:
    type: Entity
    properties:
      name: string
      address: Address
  UserList:
    type: User[]
  Tree:
    type: object
    properties:
      child?: Tree
"""


@pytest.fixture
def graph(workspace) -> Graph:
    root = workspace({'api.raml': API, 'lib.raml': LIB})
    return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))


def iris(graph: Graph, kind: str) -> list[str]:
    return [iri for iri, node in graph.nodes.items() if node.kinds[0] == kind]


class TestIris:
    """docs/16 § 3. An IRI is a promise that the same entity gets the same name."""

    def test_a_declared_type_lands_at_its_declaration_iri(self, graph):
        assert graph.find('User') == [f'{DEFAULT_BASE}/lib.raml#/declarations/types/User']

    def test_repeated_segments_are_escaped_once_per_projection(self, workspace, monkeypatch):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  A:\n    properties:\n      id: string\n'
                '  B:\n    properties:\n      id: string\n'
            }
        )
        original = graph_module._segment
        calls: list[str] = []

        def counted(value: str) -> str:
            calls.append(value)
            return original(value)

        monkeypatch.setattr(graph_module, '_segment', counted)
        build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        assert calls.count('id') == 1

    def test_a_library_is_a_unit_of_its_own_relative_to_the_entry_directory(self, graph):
        assert f'{DEFAULT_BASE}/lib.raml' in graph.nodes
        assert graph.nodes[f'{DEFAULT_BASE}/lib.raml'].attributes['name'] == 'lib.raml'

    def test_no_absolute_path_reaches_the_graph(self, graph, workspace):
        """The whole reason the root is `pyraml://id` and not the entry file URI.

        A graph carrying `file:///C:/Users/.../tmp123/lib.raml` could not be
        diffed between two runs, let alone two machines.
        """
        for iri in graph.nodes:
            assert iri.startswith(DEFAULT_BASE), iri
            assert 'file:' not in iri

    def test_a_use_site_does_not_steal_the_declaration_iri(self, graph):
        """Declarations are registered before endpoints are walked (§ 3).

        Without that pre-registration the first response body to reach `User`
        would name it, and the declared type would have an endpoint-shaped IRI.
        """
        body = graph.find('UserList')[0]
        assert body == f'{DEFAULT_BASE}/lib.raml#/declarations/types/UserList'

    def test_a_uri_template_survives_as_one_segment(self, graph):
        paths = {graph.nodes[iri].attributes['path'] for iri in iris(graph, 'EndPoint')}
        assert paths == {'/users', '/users/{userId}'}
        assert f'{DEFAULT_BASE}#/web-api/endpoint/%2Fusers%2F%7BuserId%7D' in graph.nodes

    def test_a_declaration_wins_over_a_synthetic_node_of_the_same_name(self, workspace):
        """`refs Entity` used to fail on a document this small.

        `Admin: [User, Entity]` builds a synthetic parent per branch, and each
        carries the name of the type it resolves to. So `Entity` matched both
        its own declaration and `…/types/Admin/inherits/Entity`, and the CLI
        reported an ambiguity between two nodes that are the same type — only
        one of which is somewhere an author can go.
        """
        root = workspace(
            {
                'lib.raml': '#%RAML 1.0 Library\ntypes:\n'
                '  Entity:\n    type: object\n    properties:\n      id: string\n'
                '  User:\n    type: Entity\n    properties:\n      name: string\n'
                '  Admin:\n    type: [User, Entity]\n    properties:\n      level: integer\n'
            }
        )
        graph = build_graph(parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True)))
        assert len([iri for iri, n in graph.nodes.items() if n.attributes.get('name') == 'Entity']) > 1, (
            'the collision this rule exists for must actually occur, or the test is vacuous'
        )
        assert graph.find('Entity') == [f'{DEFAULT_BASE}#/declarations/types/Entity']
        assert graph.find('User') == [f'{DEFAULT_BASE}#/declarations/types/User']

    def test_one_name_declared_in_two_libraries_stays_ambiguous(self, workspace):
        """The rule above must not paper over a real ambiguity.

        Two *declarations* of one name are a question only the caller can
        answer, so both are returned and the CLI lists them.
        """
        shared = '#%RAML 1.0 Library\ntypes:\n  Thing:\n    type: object\n    properties:\n      a: string\n'
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: D\nuses:\n  one: one.raml\n  two: two.raml\n'
                'types:\n  Uses:\n    type: object\n    properties:\n'
                '      x: one.Thing\n      y: two.Thing\n',
                'one.raml': shared,
                'two.raml': shared,
            }
        )
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        assert sorted(graph.find('Thing')) == [
            f'{DEFAULT_BASE}/one.raml#/declarations/types/Thing',
            f'{DEFAULT_BASE}/two.raml#/declarations/types/Thing',
        ]

    def test_a_whole_iri_resolves_including_one_inside_a_declaration(self, graph):
        """Which is how a caller resolves the ambiguity above."""
        inner = f'{DEFAULT_BASE}/lib.raml#/declarations/types/User/property/name'
        assert inner in graph.nodes, 'fixture moved; pick another node inside a declaration'
        assert graph.find(inner) == [inner]

    def test_two_shapes_with_one_structural_name_get_two_iris(self, workspace):
        """`type1: [string, string]` — RAML does not promise names are distinct.

        Both parents derive the IRI `.../inherits/string`, and without
        disambiguation the second merges into the first: no error, a plausible
        node count, and two types quietly become one. go-raml's converter has a
        test for the same hazard (`TestJSONLD_NoDuplicateIDs`); this hole was
        found by taking it seriously, in one corpus fixture.
        """
        root = workspace({'lib.raml': '#%RAML 1.0 Library\ntypes:\n  Both: [string, string]\n'})
        graph = build_graph(parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True)))
        parents = graph.out(graph.find('Both')[0], ['inherits'])
        assert len({edge.object for edge in parents}) == 2, 'two parents, two nodes'

    def test_the_same_parse_projects_identically_twice(self, workspace):
        """Determinism, at the level a consumer sees it."""
        root = workspace({'api.raml': API, 'lib.raml': LIB})
        options = ParseOptions(unwrap=True)
        first = build_graph(parse_from_path(root / 'api.raml', options))
        second = build_graph(parse_from_path(root / 'api.raml', options))
        assert list(first.nodes) == list(second.nodes)
        assert first.edges == second.edges


class TestTheEdgesThatAnswerQuestions:
    def test_an_operation_reaches_the_type_it_returns(self, graph):
        operation = iris(graph, 'Operation')[0]
        reached = {path.target for path in graph.walk(operation, USE_EDGES)}
        assert graph.find('User')[0] in reached
        assert graph.find('Entity')[0] in reached, 'inherited through User'

    def test_a_type_reaches_the_operations_that_can_carry_it(self, graph):
        """The query the projection exists for, run backwards."""
        found = graph.walk(graph.find('Address')[0], USE_EDGES, reverse=True)
        assert {graph.kind_of(path.target) for path in found} >= {'Operation', 'EndPoint', 'Api'}

    def test_a_route_names_the_nodes_between(self, graph):
        """The thing a SPARQL property path cannot return (docs/16 § 5)."""
        route = graph.route(iris(graph, 'Operation')[0], graph.find('User')[0], USE_EDGES)
        assert route is not None
        assert len(route.predicates) == len(route.nodes) - 1
        assert 'payload' in route.predicates
        assert 'items' in route.predicates, 'UserList is an array of User'

    def test_inheritance_survives_unwrap(self, graph):
        """docs/16 § 1.1: this edge is why there is one graph and not two."""
        parents = graph.out(graph.find('User')[0], ['inherits'])
        assert [graph.label(edge.object) for edge in parents] == ['Entity']

    def test_a_recursive_type_terminates(self, graph):
        reached = graph.walk(graph.find('Tree')[0], TYPE_EDGES)
        assert reached, 'a cyclic type still has edges'
        assert len({path.target for path in reached}) == len(reached), 'each node reached once'

    def test_a_parameter_carries_the_facts_that_belong_to_the_use(self, graph):
        """docs/16 § 2.3 — `required` is a fact about the use, not the type."""
        parameter = next(iri for iri in iris(graph, 'Parameter') if graph.label(iri) == 'limit')
        attributes = graph.nodes[parameter].attributes
        assert attributes['binding'] == 'query'
        assert attributes['required'] is False
        target = graph.out(parameter, ['range'])[0].object
        assert graph.nodes[target].attributes['maximum'] == 100

    def test_a_scheme_reaches_the_operations_that_apply_it(self, workspace):
        """A `refs` that answers this only for types would be half a tool.

        `oauth2.0` is also the corpus's reminder that a dot in a name is not
        always a namespace separator (docs/16 § 2.2).
        """
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: D\nbaseUri: https://e.test\n'
                'securitySchemes:\n  oauth2.0:\n    type: OAuth 2.0\n'
                '    settings:\n      authorizationGrants: [client_credentials]\n'
                '      accessTokenUri: https://e.test/t\n'
                '/persons:\n  get:\n    securedBy: [oauth2.0]\n'
            }
        )
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        scheme = graph.find('oauth2.0')[0]
        assert scheme.endswith('/declarations/securitySchemes/oauth2.0')
        found = graph.walk(scheme, USE_EDGES, reverse=True)
        assert 'Operation' in {graph.kind_of(route.target) for route in found}

    def test_the_reverse_index_agrees_with_the_forward_one(self, graph):
        for edge in graph.edges:
            assert edge in graph.out(edge.subject)
            assert edge in graph.into(edge.object)


class TestFacetLiterals:
    def test_a_decimal_facet_does_not_pass_through_float(self, workspace):
        """docs/16 § 2.5. `1.1` reaching a reader as `1.100000000000000088`
        would be a defect of the projection even though nothing compares it.
        """
        root = workspace(
            {
                'lib.raml': '#%RAML 1.0 Library\ntypes:\n  N:\n    type: number\n    multipleOf: 1.1\n    minimum: -0.25\n'
            }
        )
        graph = build_graph(parse_from_path(root / 'lib.raml', ParseOptions(unwrap=True)))
        attributes = graph.nodes[graph.find('N')[0]].attributes
        assert attributes['multipleOf'] == '1.1'
        assert attributes['minimum'] == '-0.25'


class TestSerialisation:
    """Checked by a real RDF parser, not by reading the output."""

    @staticmethod
    def loaded(graph: Graph, form: str):
        oxigraph = pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 § 5.1)')
        formats = {'ntriples': oxigraph.RdfFormat.N_TRIPLES, 'turtle': oxigraph.RdfFormat.TURTLE}
        store = oxigraph.Store()
        store.load(io.StringIO('\n'.join(getattr(graph, f'to_{form}')())), format=formats[form])
        return store

    @pytest.mark.parametrize('form', ['ntriples', 'turtle'])
    def test_the_output_parses_as_rdf(self, graph, form):
        assert len(self.loaded(graph, form)) > 0

    def test_both_forms_carry_the_same_statements(self, graph):
        assert len(self.loaded(graph, 'ntriples')) == len(self.loaded(graph, 'turtle'))

    def test_a_property_path_finds_what_the_walk_finds(self, graph):
        """The two interfaces have to agree, or one of them is lying.

        The alternation is built from `TYPE_EDGES` rather than written out, for
        the reason the constant is exported at all: a closure spelled twice is a
        closure that drifts, and this test would then pass by agreeing with a
        stale copy of itself.
        """
        store = self.loaded(graph, 'ntriples')
        user = graph.find('User')[0]
        alternation = '|'.join(f'raml:{name}' for name in TYPE_EDGES)
        query = f"""PREFIX raml: <{RAML_NS}>
            SELECT DISTINCT ?op WHERE {{
              ?op a raml:Operation .
              ?op raml:returns/raml:payload/raml:range/({alternation})* <{user}> .
            }}"""
        by_sparql = {str(row['op'].value) for row in store.query(query)}
        by_walk = {
            route.target
            for route in graph.walk(user, USE_EDGES, reverse=True)
            if graph.kind_of(route.target) == 'Operation' and route.predicates[-1] == 'returns'
        }
        assert by_sparql == by_walk
        assert by_walk, 'both agreeing on nothing would prove nothing'

    def test_dot_names_every_node_and_edge(self, graph):
        text = '\n'.join(graph.to_dot())
        assert text.startswith('digraph raml {')
        assert text.count(' -> ') == len(graph.edges)

    def test_json_is_serialisable(self, graph):
        payload = json.loads(json.dumps(graph.to_json()))
        assert len(payload['nodes']) == len(graph.nodes)
        assert len(payload['edges']) == len(graph.edges)


class TestTheInventory:
    """`entries` and `suggest` — docs/16 § 3.5. What a reader can name.

    `find` turns a name into a node; these two answer the question that comes
    first, which is what names there are.
    """

    def test_it_lists_declarations_from_every_file(self, graph):
        names = {name for _, name, _ in graph.entries()}
        assert {'Wrapper', 'Entity', 'Address', 'User', 'UserList', 'Tree'} <= names

    def test_it_lists_endpoints_and_operations_too(self, graph):
        by_kind: dict[str, set[str]] = {}
        for kind, name, _ in graph.entries():
            by_kind.setdefault(kind, set()).add(name)
        assert '/users/{userId}' in by_kind['EndPoint']
        assert 'get' in by_kind['Operation']

    def test_it_omits_the_nodes_inside_a_declaration(self, graph):
        """The ones reached by walking rather than by naming. On a real document
        they outnumber the declarations twenty to one.
        """
        kinds = {kind for kind, _, _ in graph.entries()}
        assert not kinds & {'Payload', 'Response', 'Property', 'Parameter', 'Request'}

    def test_every_entry_resolves_back_to_exactly_one_node(self, graph):
        """The contract the CLI depends on: a listed name is one you can use."""
        for _, _, iri in graph.entries():
            assert iri in graph.nodes

    def test_kinds_narrows(self, graph):
        assert {kind for kind, _, _ in graph.entries(['EndPoint'])} == {'EndPoint'}

    def test_it_is_stable_across_parses(self, workspace):
        """Sorted, so a reader diffing two runs sees changes and not churn."""
        root = workspace({'api.raml': API, 'lib.raml': LIB})

        def once():
            built = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
            return [(kind, name) for kind, name, _ in built.entries()]

        assert once() == once()

    def test_a_typo_is_suggested_the_near_name(self, graph):
        assert 'Address' in graph.suggest('Adress')

    def test_a_remembered_fragment_is_suggested_too(self, graph):
        """`difflib` is ratio-based, so `User` inside `UserList` scores below any
        cutoff worth using — and a half-remembered fragment is the common miss.
        """
        assert 'UserList' in graph.suggest('List')

    def test_nothing_close_suggests_nothing(self, graph):
        assert graph.suggest('zzzqqq') == []

    def test_it_never_suggests_a_name_that_is_not_listed(self, graph):
        listed = {name for _, name, _ in graph.entries()}
        for probe in ('Adress', 'List', 'user', 'get', 'Tre'):
            assert set(graph.suggest(probe)) <= listed, probe


class TestFindDoesNotSplitOneEntity:
    """docs/16 § 3.3. A node passes its name to what it contains."""

    QUERY = """#%RAML 1.0
title: T
/things:
  get:
    queryParameters:
      login?:
        type: string
        minLength: 2
    responses:
      200:
"""

    @pytest.fixture
    def parameterised(self, workspace):
        root = workspace({'api.raml': self.QUERY})
        return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_a_node_and_the_schema_inside_it_are_not_an_ambiguity(self, parameterised):
        """`…/parameter/query/login` and `…/parameter/query/login/schema` are one
        entity at two depths. Reporting the pair asked the caller to choose
        between a thing and part of itself, and `show login` exited 1 on it.
        """
        found = parameterised.find('login')
        assert len(found) == 1, found
        assert parameterised.kind_of(found[0]) == 'Parameter'

    def test_the_outer_node_is_the_one_kept(self, parameterised):
        assert not parameterised.find('login')[0].endswith('/schema')

    def test_two_declarations_of_one_name_stay_ambiguous(self, workspace):
        """Containment cannot settle that pair, and it is a real question."""
        api = '#%RAML 1.0\ntitle: T\nuses:\n  a: a.raml\n  b: b.raml\ntypes:\n  Use: a.Thing\n'
        lib = '#%RAML 1.0 Library\ntypes:\n  Thing:\n    type: object\n    properties:\n      x: string\n'
        root = workspace({'api.raml': api, 'a.raml': lib, 'b.raml': lib})
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        assert len(graph.find('Thing')) > 1

    def test_a_synthetic_parent_still_needs_the_declaration_rule(self, workspace):
        """`…/types/Admin/inherits/Entity` is not *inside* `…/types/Entity`, so
        containment cannot collapse it — only the declaration rule can. Both
        rules are load-bearing.
        """
        api = (
            '#%RAML 1.0\ntitle: T\ntypes:\n'
            '  Entity:\n    type: object\n    properties:\n      id: string\n'
            '  User:\n    type: Entity\n    properties:\n      name: string\n'
            '  Admin:\n    type: [User, Entity]\n    properties:\n      level: integer\n'
        )
        root = workspace({'api.raml': api})
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        found = graph.find('Entity')
        assert len(found) == 1, found
        assert found[0].endswith('#/declarations/types/Entity')
