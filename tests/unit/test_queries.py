"""The analysis query catalogue — docs/16-graph.md § 6.

Two things are checked, and the second is the one that matters.

**Every query is valid SPARQL and runs.** A query is a string; nothing in the
type checker or the linter looks at it, so a typo in a predicate name is
invisible until someone runs it and gets nothing back.

**Every query returns at least one row on the fixture below.** That is the same
rule `TestTheseChecksSeeSomething` applies to the corpus laws: a query that
selects nothing passes an "it ran" test just as quietly as one that works, and a
predicate renamed out from under it produces exactly that. The fixture is
therefore written to trigger *all* of them — every query owns at least one
feature of the document, and a query that stops matching fails here.
"""

from __future__ import annotations

import io

import pytest

from pyraml import ParseOptions, parse_from_path
from pyraml.views.graph import TYPE_EDGES, build_graph
from pyraml.views.queries import PREFIX, QUERIES, REACHES, render

#: Written to trigger every query in the catalogue at once: an unused type and
#: an unused trait, a multi-parent type, a recursive type, an enum, an unbounded
#: string, a required query parameter, an error response, a bodiless payload, a
#: GET with a body, an unsecured method, and an unapplied annotation type.
API = """#%RAML 1.0
title: Store
version: v1
baseUri: https://api.example.test/{version}
securitySchemes:
  oauth:
    type: OAuth 2.0
    settings:
      authorizationUri: https://example.test/authorize
      accessTokenUri: https://example.test/token
      authorizationGrants: [authorization_code]
      scopes: [read, write]
annotationTypes:
  internal: nil
  neverApplied: nil
traits:
  paged:
    queryParameters:
      offset?: integer
  neverUsed:
    description: dead
types:
  Error:
    type: object
    properties:
      code: integer
      message: string
  Entity:
    type: object
    properties:
      id:
        type: string
        maxLength: 36
  Address:
    type: object
    properties:
      city: string
      country:
        type: string
        enum: [uk, us, de]
  User:
    type: Entity
    properties:
      name: string
      address: Address
  Admin:
    type: [User, Entity]
    properties:
      level: integer
  UserList: User[]
  Node:
    type: object
    properties:
      child?: Node
  Orphan:
    type: object
    properties:
      unused: string
securedBy: [oauth]
/users:
  get:
    is: [paged]
    (internal):
    responses:
      200:
        body:
          application/json:
            type: UserList
      404:
        body:
          application/json:
            type: Error
  post:
    description: create a user
    body:
      application/json:
        type: User
    responses:
      201:
        body:
          application/json:
            type: User
  /{userId}:
    get:
      securedBy: [oauth: {scopes: [read]}]
      queryParameters:
        expand:
          type: string
          required: true
      responses:
        200:
          body:
            application/json:
              type: User
        500:
          body:
            text/plain:
/search:
  get:
    description: a GET with a body, which is legal and almost always wrong
    body:
      application/json:
        type: string
    responses:
      200:
        body:
          application/json:
            type: UserList
/health:
  get:
    securedBy: [null]
    responses:
      200:
        body:
          application/json:
"""


@pytest.fixture(scope='module')
def store(tmp_path_factory):
    """The fixture document, projected and loaded into an RDF store."""
    oxigraph = pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 § 5.1)')
    root = tmp_path_factory.mktemp('queries')
    (root / 'api.raml').write_text(API, encoding='utf-8')
    graph = build_graph(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))
    loaded = oxigraph.Store()
    loaded.load(io.StringIO('\n'.join(graph.to_ntriples())), format=oxigraph.RdfFormat.N_TRIPLES)
    return loaded


def rows(store, name: str) -> list:
    return list(store.query(render(QUERIES[name])))


class TestTheCatalogueIsWellFormed:
    def test_it_is_not_empty(self):
        assert len(QUERIES) >= 15, 'docs/16 § 6 is the decisive test; it needs enough queries to decide anything'

    def test_names_are_the_keys(self):
        assert all(name == query.name for name, query in QUERIES.items())

    def test_every_question_reads_as_a_question_a_person_would_ask(self):
        for query in QUERIES.values():
            assert query.question.endswith(('.', '?')), query.name
            assert len(query.question) > 30, query.name

    def test_importing_the_catalogue_does_not_need_a_store(self):
        """It is text. A user without `pyoxigraph` can still read and copy one."""
        import subprocess
        import sys

        done = subprocess.run(
            [sys.executable, '-c', 'import pyraml.views.queries, sys; assert "pyoxigraph" not in sys.modules'],
            capture_output=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr.decode()

    def test_the_reachability_path_matches_the_walk_closure(self):
        """`REACHES` is written out rather than generated, so it can drift.

        The two spell the same closure — one for a property path, one for
        `Graph.walk` — and a query that quietly stopped following `aliasOf`
        would report a plausible wrong answer, which is the defect docs/16
        § 2.4 already records once.
        """
        assert {term.removeprefix('raml:') for term in REACHES.split('|')} == set(TYPE_EDGES)


class TestEveryQueryRuns:
    @pytest.mark.parametrize('name', list(QUERIES), ids=list(QUERIES))
    def test_it_is_valid_sparql_and_returns_rows(self, store, name):
        found = rows(store, name)
        assert found, f'{name} matched nothing; the fixture is written to trigger every query'

    def test_the_prefixes_are_what_makes_them_runnable(self):
        """Without `render` the bare `sparql` is not a complete query."""
        assert all(render(query).startswith(PREFIX) for query in QUERIES.values())


class TestTheAnswersAreRight:
    """A query that runs and returns rows can still return the wrong ones.

    One assertion per query whose answer is a fact about the fixture rather
    than a count — the cases where a subtly wrong pattern would still produce
    output and look fine.
    """

    @staticmethod
    def column(store, name: str, variable: str) -> list[str]:
        return [row[variable].value for row in rows(store, name) if row[variable] is not None]

    def test_unused_types_finds_the_orphans_and_nothing_else(self, store):
        found = set(self.column(store, 'unused-types', 'name'))
        assert found == {'Admin', 'Orphan', 'neverApplied'}
        assert 'User' not in found, 'User is reached from a response body'

    def test_multiple_inheritance_reports_the_declaration_not_every_use(self, store):
        """The un-restricted form reports one row per *use* of the problem and
        labels most of them `application/json`, which names nothing an author
        can go and fix.
        """
        assert self.column(store, 'multiple-inheritance', 'name') == ['Admin']

    def test_unsecured_operations_respects_secured_by_null(self, store):
        """`securedBy: [null]` removes inherited security (docs/09 § A3). The
        API root secures everything, so `/health` is the only answer — and a
        query that missed the removal would return nothing at all.
        """
        assert self.column(store, 'unsecured-operations', 'path') == ['/health']

    def test_scheme_usage_carries_the_narrowed_scopes(self, store):
        assert set(self.column(store, 'scheme-usage', 'scopes')) == {'read'}

    def test_type_fan_in_counts_through_inheritance_and_arrays(self, store):
        """`Entity` is named by no body. It is reached through `User`, which is
        reached through `UserList`'s `items`, which is an *alias* — the hop
        docs/16 § 2.4 records as the one that is easy to miss.
        """
        counts = {row['name'].value: int(row['operations'].value) for row in rows(store, 'type-fan-in')}
        assert counts['Entity'] >= 3
        assert counts['Address'] == counts['User']

    def test_recursive_types_names_the_head(self, store):
        assert self.column(store, 'recursive-types', 'name') == ['Node']

    def test_trait_usage_reports_the_dead_trait_as_zero_not_as_absent(self, store):
        """The `OPTIONAL` is what does this. An inner join would omit the row,
        and an unused trait is the entire reason to run the query.
        """
        uses = {row['name'].value: int(row['uses'].value) for row in rows(store, 'trait-usage')}
        assert uses == {'paged': 1, 'neverUsed': 0}

    def test_error_response_types_finds_the_bodiless_one(self, store):
        """A 500 declaring `text/plain` with no type still has to appear, or the
        query answers "every error response is typed" by omitting the ones that
        are not. That is the `OPTIONAL`.
        """
        codes = self.column(store, 'error-response-types', 'code')
        assert set(codes) == {'404', '500'}
        typed = [row for row in rows(store, 'error-response-types') if row['type'] is not None]
        assert [row['type'].value for row in typed] == ['Error']

    def test_get_with_request_body_finds_only_the_deliberate_one(self, store):
        assert self.column(store, 'get-with-request-body', 'path') == ['/search']

    def test_required_query_parameters_ignores_the_optional_one(self, store):
        """`offset?` comes from a trait and is optional; `expand` is required."""
        assert self.column(store, 'required-query-parameters', 'name') == ['expand']

    def test_enums_reports_one_row_per_value(self, store):
        """`raml:enum` is multi-valued. It used to be a space-joined string,
        which could not represent a value containing a space — so
        `["new york", "london"]` was indistinguishable from three values.
        """
        assert set(self.column(store, 'enums', 'value')) >= {'uk', 'us', 'de'}

    def test_media_types_counts_the_long_tail(self, store):
        counts = {row['media'].value: int(row['payloads'].value) for row in rows(store, 'media-types')}
        assert counts['text/plain'] == 1
        assert counts['application/json'] > counts['text/plain']
