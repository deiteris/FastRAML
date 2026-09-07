"""The law a consumer of the projection may rely on — docs/16 § 11.7.

**A consumer descends containment, follows a link, and stops at a recursion
marker. It maintains no ancestor set.**

That last clause is the whole point of `unwrap=True`. Nine passes of RAML logic
— includes, `uses:`, type expressions, inheritance merge, traits and resource
types, overlays, security binding, annotation binding, default propagation —
happen before this, so a consumer implements none of them. What is left is two
operations, and this file is the executable statement of that.

The walker below is deliberately naive: it has no `seen` set and no depth
budget beyond a runaway ceiling. Anything it gets wrong is a leak in the
projection, not a bug in the walker.
"""

from __future__ import annotations

import pytest

from pyraml import ParseOptions, parse_from_path
from pyraml.effective import effective

#: Past this, the walker has failed to terminate. Deep enough that no legitimate
#: document reaches it, small enough to fail fast.
RUNAWAY = 60

#: Keys a consumer is not required to understand: they name parser states, not
#: RAML concepts. Listed here so this file records exactly what the contract
#: does *not* yet cover (docs/16 § 11.8).
PARSER_STATE = frozenset({'id', 'is_annotation_type', 'kind', 'link', 'type_expr'})


class RunawayError(RecursionError):
    """The naive walk did not terminate."""


def expand(node: object, depth: int = 0) -> object:
    """Expand as a consumer would, with no ancestor set.

    Two operations: descend containment, and stop at a recursion marker. A
    `$ref` is *not* followed — the consumer looks it up when it wants to, and
    the point here is that containment alone terminates.
    """
    if depth > RUNAWAY:
        raise RunawayError
    if isinstance(node, dict):
        if node.get('type') == 'recursive':
            # The marker is the contract: the structure repeats from here and
            # must not be expanded. Without it, this walk would not terminate
            # and every consumer would need its own ancestor set.
            return f'<recursive {node.get("name")}>'
        if set(node) == {'$ref'}:
            return f'<link {node["$ref"]}>'
        return {key: expand(value, depth + 1) for key, value in node.items() if key not in PARSER_STATE}
    if isinstance(node, list):
        return [expand(item, depth + 1) for item in node]
    return node


CYCLES = {
    'self': 'types:\n  A:\n    properties:\n      a?: A\n',
    'mutual': 'types:\n  A:\n    properties:\n      b?: B\n  B:\n    properties:\n      a?: A\n',
    'through an array': 'types:\n  A:\n    properties:\n      kids?: A[]\n',
    'through a union': 'types:\n  A:\n    properties:\n      n?: A | nil\n',
    'three deep': (
        'types:\n  A:\n    properties:\n      b?: B\n'
        '  B:\n    properties:\n      c?: C\n'
        '  C:\n    properties:\n      a?: A\n'
    ),
}


def project(workspace, body: str) -> object:
    root = workspace({'api.raml': '#%RAML 1.0\ntitle: T\n' + body})
    return effective(parse_from_path(root / 'api.raml', ParseOptions(unwrap=True)))


class TestANaiveWalkTerminates:
    """Every cycle is broken by a marker, whatever route it takes.

    A cycle can close through a property, an array's items or a union member,
    and P9 marks each. If any shape of cycle were unmarked, a consumer would
    hang on a document that parses cleanly — the worst failure available,
    because nothing reports it.
    """

    @pytest.mark.parametrize('name', list(CYCLES))
    def test_without_an_ancestor_set(self, workspace, name):
        expand(project(workspace, CYCLES[name]))

    def test_the_marker_is_what_stops_it(self, workspace):
        """Guards the guard: prove the walker would run away without the rule."""
        projection = project(workspace, CYCLES['self'])

        def unguarded(node: object, depth: int = 0) -> object:
            if depth > RUNAWAY:
                raise RunawayError
            if isinstance(node, dict):
                return {key: unguarded(value, depth + 1) for key, value in node.items()}
            if isinstance(node, list):
                return [unguarded(item, depth + 1) for item in node]
            return node

        # The marker carries `head`, which is a `$ref` back to the declaration,
        # so a walker that does not stop at the marker is finite only because
        # `$ref` is a leaf here. What it loses is the *signal*: it cannot tell a
        # repeat from a fresh subtree.
        walked = unguarded(projection)
        assert 'recursive' in str(walked), 'the marker must be visible to a walker that ignores it'


class TestALinkIsNotARecursionMarker:
    """The two must stay distinguishable.

    Collapsing them into a bare `$ref` is what forces an ancestor set on the
    consumer — the cost Scalar pays and Redoc, Stoplight and swagger-js each
    avoid with a distinct marker of their own.
    """

    def test_a_cycle_and_a_plain_reuse_look_different(self, workspace):
        projection = project(
            workspace,
            'types:\n  Money:\n    properties:\n      amount: number\n'
            '  Node:\n    properties:\n      cost: Money\n      next?: Node\n',
        )
        node = projection['types']['api.raml']['Node']['properties']
        assert node['next']['type']['type'] == 'recursive'
        assert node['cost']['type'].get('type') != 'recursive'


class TestAnAliasReadsAsItsReferent:
    """`Prices: Price[]` puts an *alias* of `Price` under `items`, and the alias
    is a parser mechanism with no RAML meaning (docs/07 § 3.6).

    A consumer reading the tree honestly sees an anonymous object whose
    `inherits` names `Money` — `Price`'s supertype — and concludes "an array of
    anonymous things extending Money". That is a wrong answer, not a missing
    one, and it is the failure this law exists to catch. 427 alias nodes occur
    across the TCK corpus; 200 read as anonymous and 50 carry a misleading
    `inherits`.

    Unlike the recursion marker, nothing is lost by making it transparent: an
    alias shares its referent's containers, so it holds no facets of its own.
    Every one of the 267 alias shapes in the corpus targets a declaration, so
    the reference always resolves.
    """

    def test_items_of_an_array_of_a_declared_type_is_that_type(self, workspace):
        projection = project(
            workspace,
            'types:\n  Money:\n    properties:\n      amount: number\n'
            '  Price:\n    type: Money\n    properties:\n      vat?: number\n'
            '  Prices: Price[]\n',
        )
        items = projection['types']['api.raml']['Prices']['items']
        assert set(items) == {'$ref'}, f'expected a link to Price, got a {items.get("name")!r} node'
        assert items['$ref'].endswith('/types/Price')


class TestEveryReferenceResolvesInsideTheTree:
    """A tree consumer has only the tree.

    Law 15 checks addresses against the *graph*, which is a different output. It
    passed while every annotation application in the corpus — 318 of 318 —
    pointed at an address the tree did not contain, because annotation types had
    no section of their own. A reference a consumer cannot follow with what it
    was given is a dangling reference, whatever some other output holds.
    """

    DOCUMENT = """types:
  Money:
    properties:
      amount: number
  Price:
    type: Money
    (tier): gold
/things:
  get:
    (tier): silver
    responses:
      200:
        body:
          application/json:
            type: Price
"""

    @pytest.fixture
    def projection(self, workspace):
        return project(workspace, 'annotationTypes:\n  tier: string\n' + self.DOCUMENT)

    def addresses(self, node: object, found: list[str] | None = None) -> list[str]:
        """Every address the projection emits as a *reference*, not as an `id`."""
        found = [] if found is None else found
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('$ref', 'type', 'declaration') and isinstance(value, str) and value.startswith('pyraml://'):
                    found.append(value)
                self.addresses(value, found)
        elif isinstance(node, list):
            for item in node:
                self.addresses(item, found)
        return found

    def declared(self, node: object, found: set[str] | None = None) -> set[str]:
        found = set() if found is None else found
        if isinstance(node, dict):
            if isinstance(node.get('id'), str):
                found.add(node['id'])
            for value in node.values():
                self.declared(value, found)
        elif isinstance(node, list):
            for item in node:
                self.declared(item, found)
        return found

    def test_an_annotation_type_is_a_section_of_its_own(self, projection):
        assert list(projection['annotation_types']['api.raml']) == ['tier']

    def test_every_reference_names_a_node_the_tree_carries(self, projection):
        present = self.declared(projection)
        dangling = sorted({ref for ref in self.addresses(projection) if ref not in present})
        assert not dangling, f'{len(dangling)} references resolve nowhere in the tree: {dangling}'

    def test_an_annotation_applied_anywhere_reaches_its_declaration(self, projection):
        declaration = projection['annotation_types']['api.raml']['tier']['id']
        on_type = projection['types']['api.raml']['Price']['annotations'][0]
        on_operation = projection['endpoints']['/things']['operations']['get']['annotations'][0]
        assert on_type['type'] == declaration
        assert on_operation['type'] == declaration


class TestACycleIsAlwaysAMarkerNeverABareLink:
    """The projector closes a cycle P9 did not mark, and must spell it the same.

    That path fired 31 times across the corpus, emitting a bare `$ref` — which a
    consumer expanding links cannot tell from an ordinary reference, so it would
    re-enter and loop. One meaning, one spelling.
    """

    def test_no_bare_ref_stands_where_a_walk_re_entered(self, workspace):
        """Expand links as an inlining consumer would, stopping only at markers."""
        projection = project(
            workspace,
            'types:\n  A:\n    properties:\n      b?: B\n  B:\n    properties:\n      a?: A\n',
        )
        declarations = projection['types']['api.raml']

        def inline(node: object, depth: int = 0) -> object:
            if depth > RUNAWAY:
                raise RunawayError
            if isinstance(node, dict):
                if node.get('type') == 'recursive':
                    return '<recursive>'
                if set(node) == {'$ref'}:
                    target = node['$ref'].rsplit('/', 1)[-1]
                    # A link to a declaration is followable by design; that is
                    # what makes it a link rather than a marker.
                    return inline(declarations[target], depth + 1) if target in declarations else '<link>'
                return {k: inline(v, depth + 1) for k, v in node.items()}
            if isinstance(node, list):
                return [inline(i, depth + 1) for i in node]
            return node

        inline(declarations)
