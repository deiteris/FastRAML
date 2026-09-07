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


@pytest.mark.xfail(reason='docs/16 § 11.8: an alias is parser machinery and must become transparent', strict=True)
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
