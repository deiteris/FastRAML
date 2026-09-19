"""The graph projection — docs/16-graph.md.

Three things are worth pinning here and nothing else is:

- **the IRI scheme**, because every consumer and every cached query result
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

import fastraml.nodes as nodes_module
import fastraml.views.walk as walk_module
from fastraml import ParseOptions, parse_from_path
from fastraml.views.graph import (
    DEFAULT_BASE,
    RAML_NS,
    TYPE_EDGES,
    USE_EDGES,
    Graph,
    GraphNode,
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
        original = walk_module._segment
        calls: list[str] = []

        def counted(value: str) -> str:
            calls.append(value)
            return original(value)

        monkeypatch.setattr(walk_module, '_segment', counted)
        build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        assert calls.count('id') == 1

    def test_a_library_is_a_unit_of_its_own_relative_to_the_entry_directory(self, graph):
        assert f'{DEFAULT_BASE}/lib.raml' in graph.nodes
        assert graph.nodes[f'{DEFAULT_BASE}/lib.raml'].attributes['name'] == 'lib.raml'

    def test_no_absolute_path_reaches_the_graph(self, graph, workspace):
        """The whole reason the root is `fastraml://id` and not the entry file URI.

        A graph carrying `file:///C:/Users/.../tmp123/lib.raml` could not be
        diffed between two runs, let alone two machines.
        """
        for iri in graph.nodes:
            assert iri.startswith(DEFAULT_BASE), iri
            assert 'file:' not in iri

    def test_a_subschema_is_addressed_by_its_own_document(self, workspace):
        """docs/16 § 3.3. A `$ref` target is one thing however many RAML types
        reach it, so its address comes from its document and JSON Pointer — the
        way `unit()` makes a library type independent of who imports it.

        Addressing it by containment gave the same subschema a different IRI per
        referencing type, and the one that got there first lent its name to the
        rest.
        """
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  A: !include a.json\n  B: !include b.json\n',
                'a.json': '{"type": "object", "properties": {"m": {"$ref": "shared.json"}}}',
                'b.json': '{"type": "object", "properties": {"m": {"$ref": "shared.json"}}}',
                'shared.json': '{"type": "object", "properties": {"x": {"type": "string"}}}',
            }
        )
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        shared = f'{DEFAULT_BASE}/shared.json#'
        assert shared in graph.nodes, 'the shared schema is a node in its own right'
        assert f'{shared}/properties/x' in graph.nodes
        # Reached from both, and neither declaration owns it.
        assert '/declarations/types/A' not in shared
        assert '/declarations/types/B' not in shared

    def test_an_inline_schema_stays_with_its_declaration(self, workspace):
        """It compiles under the RAML file's own URI, so it has no address
        independent of the type that wrote it — and two inline schemas in one
        file would otherwise both claim `#/properties/x`.
        """
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n'
                '  A:\n    type: |\n      {"type": "object", "properties": {"x": {"type": "string"}}}\n'
            }
        )
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        assert any('/declarations/types/A' in iri and iri.endswith('/property/x/schema') for iri in graph.nodes)

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

    def test_request_shapes_are_a_first_class_derived_index(self, graph):
        request_shapes = graph.request_shape_iris()
        query = next(iri for iri in iris(graph, 'Parameter') if graph.label(iri) == 'limit')
        uri = next(iri for iri in iris(graph, 'Parameter') if graph.label(iri) == 'userId')

        assert graph.out(query, ('range',))[0].object in request_shapes
        assert graph.out(uri, ('range',))[0].object in request_shapes
        assert graph.find('User')[0] not in request_shapes, 'response-only type'
        assert graph.request_shape_iris() is request_shapes, 'derived once per graph'

    def test_base_uri_parameter_shape_is_request_input(self, workspace):
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\nbaseUri: https://{tenant}.example.test\n'
                'types:\n  Tenant: string\nbaseUriParameters:\n  tenant: Tenant\n'
            }
        )
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        parameter = next(iri for iri in iris(graph, 'Parameter') if graph.label(iri) == 'tenant')
        assert graph.out(parameter, ('range',))[0].object in graph.request_shape_iris()

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


SCHEMA_LIB = """#%RAML 1.0
title: Schemas
traits:
  paged:
    queryParameters:
      offset?: integer
securitySchemes:
  key:
    type: Pass Through
    describedBy:
      headers:
        X-Key: string
types:
  Err: !include err.json
/things:
  get:
    is: [paged]
    securedBy: [key]
    responses:
      200:
        body:
          application/json: Err
"""

ERR = """{
  "type": "object",
  "allOf": [
    {"properties": {"code": {"type": "integer"}}, "required": ["code"]},
    {"properties": {"tags": {"type": "array", "items": {"type": "string"}}}}
  ]
}"""


class TestSchemaTypesAreNotLeaves:
    """docs/16 § 2.6. The graph walked the unprojected shape.

    A `JsonShape` holds no `ScalarFacet` slots and no properties, so a schema
    type had no children and no attributes: `deps` reported it was made of
    nothing, SPARQL queries over `raml:property` skipped it, and nothing reading
    the projection saw any change when a whole schema was replaced.
    """

    @pytest.fixture
    def schema_graph(self, workspace):
        root = workspace({'api.raml': SCHEMA_LIB, 'err.json': ERR})
        return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_a_schema_type_has_its_properties_as_children(self, schema_graph):
        err = schema_graph.find('Err')[0]
        names = {schema_graph.label(e.object) for e in schema_graph.out(err, ('property',))}
        assert {'code', 'tags'} <= names

    def test_an_all_of_member_without_a_type_still_projects(self, schema_graph):
        """`{"properties": {...}}` with no `"type"` is an object constraint --
        the enclosing schema already said `"type": "object"`. Projecting it as
        `any` made `inherit` refuse and took the whole projection down.
        """
        err = schema_graph.find('Err')[0]
        assert schema_graph.out(err, ('property',)), 'allOf members contributed nothing'

    def test_the_walk_reaches_inside_a_schema(self, schema_graph):
        err = schema_graph.find('Err')[0]
        reached = {schema_graph.label(r.target) for r in schema_graph.walk(err, TYPE_EDGES)}
        assert 'code' in reached


class TestLabelsIdentifyTheNode:
    def test_an_anonymous_member_reads_as_its_type(self, graph):
        """The model names an array's member `items`, so a route ended
        `-items-> items`: the hop just followed, and nothing about the node.
        """
        user_list = graph.find('UserList')[0]
        members = [graph.label(e.object) for e in graph.out(user_list, ('items',))]
        assert members
        assert 'items' not in members

    def test_a_declaration_keeps_its_own_name(self, graph):
        """`types/User` repeats its segment too, and there the repeat is the
        name a person wrote.
        """
        assert graph.label(graph.find('User')[0]) == 'User'

    def test_an_operation_still_reads_as_its_method(self, graph):
        """An operation's name defaults to the method, which also spells its
        segment -- and `get` is exactly what a reader wants there.
        """
        kinds = {graph.label(i) for i, n in graph.nodes.items() if n.kinds[0] == 'Operation'}
        assert 'get' in kinds


class TestDeclarationsArePositioned:
    """A trait is applied far from where it is written, so the location column
    is the whole reason to list it. All three kinds had an empty one.
    """

    @pytest.fixture
    def positioned(self, workspace):
        root = workspace({'api.raml': SCHEMA_LIB, 'err.json': ERR})
        return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    @pytest.mark.parametrize(('kind', 'name'), [('Trait', 'paged'), ('SecurityScheme', 'key')])
    def test_it_has_a_file_and_a_line(self, positioned, kind, name):
        node = positioned.nodes[positioned.find(name)[0]]
        assert node.attributes.get('definedIn')
        assert node.attributes.get('line')


class TestEveryNodeIsBackedByTheModel:
    """docs/16 section 1: a projection holds references, and invents nothing.

    A node with no model object behind it would be something this layer made
    up. The type checker is what enforces that at the fifteen places a node is
    created — `GraphNode.entity` is not optional — and these pin the two
    consequences a reader can observe.
    """

    def test_no_node_stands_for_nothing(self, graph: Graph):
        assert [iri for iri, node in graph.nodes.items() if node.entity is None] == []

    def test_the_kinds_that_used_to_go_unrecorded_are_recorded(self, graph: Graph):
        """Types and endpoints were kept in two side maps; the rest were not."""
        by_kind = {node.kinds[0]: type(node.entity).__name__ for node in graph.nodes.values()}
        assert by_kind['Property'] == 'Property'
        assert by_kind['Parameter'] == 'Parameter'
        assert by_kind['Payload'] == 'Body'
        assert by_kind['Response'] == 'Response'
        assert by_kind['Request'] == 'Request'
        assert by_kind['Unit'] in {'APIFragment', 'Library'}

    def test_entity_at_is_what_the_narrowing_helpers_are_built_from(self, graph: Graph):
        endpoint = iris(graph, 'EndPoint')[0]
        assert graph.entity_at(endpoint) is graph.endpoint_at(endpoint)
        # A type node is not an endpoint, and asking does not raise.
        declared = graph.find('User')[0]
        assert graph.endpoint_at(declared) is None
        assert graph.shape_at(declared) is graph.entity_at(declared)

    def test_an_unknown_iri_has_no_entity(self, graph: Graph):
        assert graph.entity_at(f'{DEFAULT_BASE}#/nope') is None


class TestNameIsTheCheapPathToTheSameAnswer:
    """docs/12 § 19e: `GraphNode.name` exists so `find` and `label` need not
    build a whole attribute dictionary to read one key.

    It is a second expression of something `attributes` already says, which is
    exactly the shape that rots. The rule is that `attributes` reads the
    property rather than repeating the expression, and this is what holds the
    two together — it caught four node kinds whose name arrives through
    `_named` rather than as a literal key, and one, `ParameterNode`, that spells
    it directly.
    """

    def test_every_node_agrees_with_its_own_attributes(self, graph: Graph):
        disagreed = {
            iri: (node.name, node.attributes.get('name', ''))
            for iri, node in graph.nodes.items()
            if node.name != node.attributes.get('name', '')
        }
        assert disagreed == {}

    #: `DeclaredNode` — trait, resource type and security scheme — is one of the
    #: two classes whose name arrives from a shared base, and the module's own
    #: `graph` fixture declares none of the three. The worked document does.
    WORKED = 'fixtures/sample/api.raml'

    @pytest.fixture
    def declaring(self) -> Graph:
        options = ParseOptions(unwrap=True, workspace_root='fixtures')
        return build_graph(parse_from_path(self.WORKED, options))

    def test_the_declared_kinds_agree_too(self, declaring: Graph):
        """Traits, resource types and security schemes reach `name` through
        `DeclaredNode`, which the module fixture never builds."""
        assert {node.kinds[0] for node in declaring.nodes.values()} >= {
            'ResourceType',
            'SecurityScheme',
            'Trait',
        }
        disagreed = [iri for iri, node in declaring.nodes.items() if node.name != node.attributes.get('name', '')]
        assert disagreed == []

    def test_every_node_kind_is_covered_by_that(self, graph: Graph, declaring: Graph):
        """The agreement above is worth nothing if a kind is absent from both
        documents, so the kinds they actually exercised are pinned here."""
        reached = {node.kinds[0] for node in graph.nodes.values()}
        reached |= {node.kinds[0] for node in declaring.nodes.values()}
        assert reached >= {
            'Api',
            'EndPoint',
            'Operation',
            'Parameter',
            'PatternProperty',
            'Payload',
            'Property',
            'Request',
            'ResourceType',
            'Response',
            'SecurityScheme',
            'Trait',
            'Type',
            'Unit',
        }

    def test_a_node_with_no_name_reports_empty_rather_than_none(self, graph: Graph):
        payloads = [graph.nodes[iri] for iri in iris(graph, 'Payload')]
        assert payloads
        assert all(node.name == '' for node in payloads)


class TestAttributesAreDerivedNotStored:
    """docs/16 section 2.8: this layer owns the vocabulary, not the values."""

    def test_a_node_stores_no_attribute_dict(self, graph: Graph):
        node = graph.nodes[graph.find('User')[0]]
        assert 'attributes' not in GraphNode.__slots__
        # Read twice, equal both times, and not the same object either time.
        first, second = node.attributes, node.attributes
        assert first == second
        assert first is not second

    def test_a_facet_added_to_a_kind_needs_no_change_here(self, graph: Graph):
        """Facets are read off the instance's `__slots__`, not a table."""
        limit = graph.find('limit')
        assert graph.nodes[limit[0]].attributes['binding'] == 'query'
        target = graph.out(limit[0], ['range'])[0].object
        assert graph.nodes[target].attributes['maximum'] == 100

    def test_an_operation_does_not_restate_its_endpoints_path(self, graph: Graph):
        """A fact reachable by following an edge is not an attribute.

        The endpoint holds the path and `supportedOperation` reaches it. Storing
        it made a moved resource report a change once per method beneath it
        rather than once at the edge.
        """
        operation = iris(graph, 'Operation')[0]
        assert 'path' not in graph.nodes[operation].attributes
        endpoint = graph.into(operation, ['supportedOperation'])[0].subject
        assert graph.nodes[endpoint].attributes['path'] == '/users/{userId}'

    def test_scopes_are_every_scheme_in_force_not_the_last_one(self, workspace):
        """The eager writer set `scopes` once per scheme inside the loop."""
        root = workspace(
            {
                'api.raml': """#%RAML 1.0
title: T
securitySchemes:
  first:
    type: OAuth 2.0
    settings:
      authorizationUri: https://e.test/a
      accessTokenUri: https://e.test/t
      authorizationGrants: [authorization_code]
      scopes: [read, write]
  second:
    type: OAuth 2.0
    settings:
      authorizationUri: https://e.test/a
      accessTokenUri: https://e.test/t
      authorizationGrants: [authorization_code]
      scopes: [admin]
/things:
  get:
    securedBy: [first: {scopes: [read]}, second: {scopes: [admin]}]
"""
            }
        )
        graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
        operation = iris(graph, 'Operation')[0]
        assert graph.nodes[operation].attributes['scopes'] == ('read', 'admin')


class TestAKindAndItsEntityCannotDiverge:
    """docs/16 section 2.7: a node's kind is its class.

    `TypeNode` holds a `BaseShape`, `ResponseNode` holds a `Response`. The
    entity's type is a parameter of the class, so a mismatched pair does not
    typecheck and there is nothing to assert at runtime. These pin the two
    things a reader can still observe.
    """

    def test_the_kind_comes_from_the_class(self, graph: Graph):
        for node in graph.nodes.values():
            assert node.kinds[0] == type(node).kind

    def test_a_type_node_names_its_shape_class_second(self, graph: Graph):
        node = graph.nodes[graph.find('User')[0]]
        assert node.kinds == ('Type', 'ObjectShape')

    def test_every_kind_in_the_vocabulary_has_exactly_one_class(self, graph: Graph):
        """Except the three a name can fail to resolve to, which have two."""
        by_kind: dict[str, set[str]] = {}
        for node in graph.nodes.values():
            by_kind.setdefault(node.kinds[0], set()).add(type(node).__name__)
        assert by_kind['Type'] == {'TypeNode'}
        assert by_kind['EndPoint'] == {'EndPointNode'}
        assert by_kind['Unit'] == {'UnitNode'}


QUALIFIED_LIB = """#%RAML 1.0 Library
traits:
  paged:
    queryParameters:
      offset?: integer
resourceTypes:
  collection:
    get:
      description: list them
annotationTypes:
  audited: boolean
securitySchemes:
  key:
    type: Pass Through
    describedBy:
      headers:
        X-Key: string
"""

QUALIFIED_API = """#%RAML 1.0
title: Qualified
uses:
  shared: shared.raml
/things:
  type: shared.collection
  (shared.audited): true
  get:
    is: [shared.paged]
    securedBy: [shared.key]
"""


class TestAReferenceThroughALibraryReachesTheDeclaration:
    """docs/16 section 2.2: `appliesTrait`, `appliesResourceType`, `securedBy`
    and `annotation` point at the declaration, not at a stand-in for it.

    A qualified name is the case that matters. `shared.paged` names one trait in
    one library, and a projection that cannot reach it emits an edge to a node
    that holds only the name — an application that looks recorded and answers
    `refs shared.paged` with nothing.
    """

    @pytest.fixture
    def graph(self, workspace) -> Graph:
        root = workspace({'api.raml': QUALIFIED_API, 'shared.raml': QUALIFIED_LIB})
        return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def declaration(self, graph: Graph, bucket: str, name: str) -> str:
        tail = f'#/declarations/{bucket}/{name}'
        found = [iri for iri in graph.nodes if iri.endswith(tail)]
        assert len(found) == 1, found
        return found[0]

    def test_a_qualified_trait_reaches_the_library_declaration(self, graph: Graph):
        operation = iris(graph, 'Operation')[0]
        target = self.declaration(graph, 'traits', 'paged')
        assert [e.object for e in graph.out(operation, ['appliesTrait'])] == [target]
        # It is the declaration, not a stand-in: it knows where it was written.
        assert graph.nodes[target].attributes['definedIn'] == 'shared.raml'

    def test_a_qualified_resource_type_reaches_the_library_declaration(self, graph: Graph):
        endpoint = iris(graph, 'EndPoint')[0]
        target = self.declaration(graph, 'resourceTypes', 'collection')
        assert [e.object for e in graph.out(endpoint, ['appliesResourceType'])] == [target]
        assert graph.nodes[target].attributes['definedIn'] == 'shared.raml'

    def test_a_qualified_scheme_reaches_the_library_declaration(self, graph: Graph):
        operation = iris(graph, 'Operation')[0]
        target = self.declaration(graph, 'securitySchemes', 'key')
        assert [e.object for e in graph.out(operation, ['securedBy'])] == [target]

    def test_a_qualified_annotation_reaches_its_type(self, graph: Graph):
        endpoint = iris(graph, 'EndPoint')[0]
        target = self.declaration(graph, 'annotations', 'audited')
        assert [e.object for e in graph.out(endpoint, ['annotation'])] == [target]

    def test_refs_answers_from_the_declaration_side(self, graph: Graph):
        """The reverse walk is the question the edges exist for."""
        trait = self.declaration(graph, 'traits', 'paged')
        assert [e.subject for e in graph.into(trait, ['appliesTrait'])] == iris(graph, 'Operation')

    def test_no_node_stands_in_for_a_name_that_resolved(self, graph: Graph):
        """An `Unresolved*Node` here means a reference was matched by name and
        missed, which is the failure the model reference exists to prevent."""
        stood_in = [n.iri for n in graph.nodes.values() if type(n).__name__.startswith('Unresolved')]
        assert stood_in == []


class TestOneNameInTwoLibraries:
    """docs/16 section 2.2: an application points at what was applied.

    `a.paged` and `b.paged` are one name in two libraries. Matching the name
    cannot tell them apart and returns whichever was declared first, so the
    edge lands on a trait that was never applied and `refs a.paged` reports a
    use that is not there. The reference carries the declaration P6 resolved.
    """

    @pytest.fixture
    def graph(self, workspace) -> Graph:
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\nuses:\n  a: a.raml\n  b: b.raml\n'
                '/things:\n  get:\n    is: [b.paged]\n',
                'a.raml': '#%RAML 1.0 Library\ntraits:\n  paged:\n    queryParameters:\n      fromA?: integer\n',
                'b.raml': '#%RAML 1.0 Library\ntraits:\n  paged:\n    queryParameters:\n      fromB?: integer\n',
            }
        )
        return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_the_edge_lands_on_the_library_that_was_applied(self, graph: Graph):
        operation = iris(graph, 'Operation')[0]
        applied = [e.object for e in graph.out(operation, ['appliesTrait'])]
        assert applied == [f'{DEFAULT_BASE}/b.raml#/declarations/traits/paged']

    def test_the_edge_agrees_with_what_the_merge_produced(self, graph: Graph):
        """The parameter the trait contributed says which one really applied."""
        assert [graph.label(iri) for iri in iris(graph, 'Parameter')] == ['fromB']

    def test_the_trait_that_was_not_applied_has_no_uses(self, graph: Graph):
        unused = f'{DEFAULT_BASE}/a.raml#/declarations/traits/paged'
        assert graph.into(unused, ['appliesTrait']) == []


class TestTheProjectionRules:
    """docs/16 section 1: what "a projection" forbids, one test per clause.

    The rules are here rather than spread across the classes above because each
    is a property of the whole layer, and each was stated as prose long enough
    to be violated without anyone noticing.
    """

    def test_a_node_stores_only_its_iri_entity_and_root(self):
        """Clause 1: references, not copies.

        A field restating something the entity holds is a second copy of the
        model. Adding one fails here, which is the point.
        """
        allowed = {'iri', 'entity', 'root', 'shape_kind'}
        for name in nodes_module.__all__:
            cls = getattr(nodes_module, name)
            if not (isinstance(cls, type) and issubclass(cls, GraphNode)):
                continue
            stored = {slot for klass in cls.__mro__ for slot in getattr(klass, '__slots__', ())}
            assert stored <= allowed, (name, stored - allowed)

    def test_attributes_is_computed_and_not_a_field(self, graph: Graph):
        """Clause 2: the vocabulary is owned, the values are not."""
        assert isinstance(type(graph.nodes[graph.find('User')[0]]).attributes, property)
        node = graph.nodes[graph.find('User')[0]]
        assert node.attributes == node.attributes
        assert node.attributes is not node.attributes

    def test_no_node_kind_is_reachable_without_a_model_object(self, graph: Graph):
        """Clause 1 again, from the other side: nothing is invented."""
        assert all(node.entity is not None for node in graph.nodes.values())

    def test_an_operation_does_not_restate_a_fact_an_edge_reaches(self, graph: Graph):
        """The corollary: the endpoint's path is one hop away, so it is not here."""
        for iri in iris(graph, 'Operation'):
            assert 'path' not in graph.nodes[iri].attributes


class TestEveryFileThatDeclaresSomethingHasANode:
    """docs/16 section 2.2: `definedIn` names a file, and that file is a node.

    A typed fragment is one declaration and has no `types:` map, so walking the
    maps never reaches it — it is reached as a parent of whatever `types:` entry
    included it. Its file gets a node all the same, or `definedIn` names
    something the graph does not contain and no traversal can reach the file.
    """

    @pytest.fixture
    def graph(self, workspace) -> Graph:
        root = workspace(
            {
                'api.raml': '#%RAML 1.0\ntitle: T\ntypes:\n  Role: !include role.raml\n'
                '/roles:\n  get:\n    responses:\n      200:\n        body:\n'
                '          application/json: Role\n',
                'role.raml': '#%RAML 1.0 DataType\ntype: object\nproperties:\n  name: string\n',
            }
        )
        return build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))

    def test_the_included_file_is_a_node(self, graph: Graph):
        assert sorted(graph.label(iri) for iri in iris(graph, 'Unit')) == ['api.raml', 'role.raml']

    def test_the_file_declares_what_it_holds(self, graph: Graph):
        unit = next(i for i in iris(graph, 'Unit') if graph.label(i) == 'role.raml')
        declared = [e.object for e in graph.out(unit, ['declares'])]
        assert len(declared) == 1
        assert graph.nodes[declared[0]].attributes['definedIn'] == 'role.raml'

    def test_declares_agrees_with_defined_in(self, graph: Graph):
        """A file declares what is written in it, not what names it elsewhere."""
        for edge in graph.edges:
            if edge.predicate != 'declares':
                continue
            where = graph.nodes[edge.object].attributes.get('definedIn')
            assert where is None or where == graph.nodes[edge.subject].attributes['name']

    def test_no_unit_node_is_unreachable(self, graph: Graph):
        for iri in iris(graph, 'Unit'):
            assert graph.out(iri) or graph.into(iri), iri
