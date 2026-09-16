"""What changed, and what it breaks — docs/16-graph.md § 10.

The change list is mechanical and easy to check. The classification is not, and
almost all of these are about it, because **the same edit is breaking on one
side of the wire and harmless on the other**. A rule table that got the
direction backwards would still produce confident, well-formatted output.
"""

from __future__ import annotations

import pytest

from fastraml import ParseOptions, parse_from_path
from fastraml.views.diff import RULES, _side_map, classify, diff, record
from fastraml.views.graph import build_graph

BASE = """#%RAML 1.0
title: Orders
types:
  Order:
    type: object
    properties:
      id: string
      discount: number
      channel:
        type: string
        enum: [web, phone]
  NewOrder:
    type: object
    properties:
      sku:
        type: string
        maxLength: 32
      note?: string
/orders:
  get:
    responses:
      200:
        body:
          application/json: Order
  post:
    body:
      application/json: NewOrder
    responses:
      201:
        body:
          application/json: Order
/legacy:
  get:
    responses:
      200:
"""


@pytest.fixture
def changes(workspace):
    """Diff `BASE` against an edited copy, and index by rule name."""

    def apply(*edits: tuple[str, str], drop: tuple[str, ...] = ()):
        after = BASE
        for old, new in edits:
            assert old in after, old
            after = after.replace(old, new)
        for text in drop:
            assert text in after, text
            after = after.replace(text, '')
        root = workspace({'v1.raml': BASE, 'v2.raml': after})
        options = ParseOptions(unwrap=True)
        old_graph = build_graph(parse_from_path(root / 'v1.raml', options))
        new_graph = build_graph(parse_from_path(root / 'v2.raml', options))
        found: dict[str, list] = {}
        for change in diff(old_graph, new_graph):
            found.setdefault(classify(change).name, []).append(change)
        return found

    return apply


class TestTheRuleTableItself:
    def test_every_rule_explains_itself(self):
        for rule in RULES.values():
            assert rule.because, rule.name
            assert rule.severity in ('breaking', 'risky', 'safe', 'cosmetic')

    def test_an_unrecognised_change_is_risky_not_safe(self):
        """Silence about something unclassified is the one answer that misleads."""
        assert RULES['other'].severity == 'risky'


class TestDirectionDecidesSeverity:
    """The crux. Each pair is one edit, breaking on one side and not the other."""

    def test_a_property_removed_breaks_a_response_and_not_a_request(self, changes):
        found = changes(drop=('      discount: number\n',))
        assert 'response-property-removed' in found
        assert RULES['response-property-removed'].severity == 'breaking'
        assert RULES['request-property-removed'].severity != 'breaking'

    def test_a_property_becoming_required_breaks_a_request(self, changes):
        found = changes(('      note?: string', '      note: string'))
        assert 'request-property-required' in found
        assert RULES['request-property-required'].severity == 'breaking'
        assert RULES['request-property-optional'].severity == 'safe'

    def test_a_new_required_request_property_is_breaking(self, changes):
        """Adding a field a client must now send rejects every request that
        omits it. The rule that once called all additions safe read this as
        safe and let the break through."""
        found = changes(('      note?: string', '      note?: string\n      coupon: string'))
        assert 'request-property-added-required' in found
        assert RULES['request-property-added-required'].severity == 'breaking'

    def test_a_new_optional_request_property_is_safe(self, changes):
        found = changes(('      note?: string', '      note?: string\n      coupon?: string'))
        assert 'request-property-added' in found
        assert RULES['request-property-added'].severity == 'safe'
        assert 'request-property-added-required' not in found

    def test_a_response_property_becoming_required_is_safe(self, workspace):
        """Mirror of the request case: a field callers already accept as absent
        is now guaranteed present, so nothing that worked stops working. This
        cell used to have no rule, and `classify` raised `KeyError` on it.
        """
        base = """#%RAML 1.0
title: T
types:
  Report:
    type: object
    properties:
      code: string
      summary?: string
/reports:
  get:
    responses:
      200:
        body:
          application/json: Report
"""
        root = workspace({'v1.raml': base, 'v2.raml': base.replace('summary?: string', 'summary: string')})
        options = ParseOptions(unwrap=True)
        old = build_graph(parse_from_path(root / 'v1.raml', options))
        new = build_graph(parse_from_path(root / 'v2.raml', options))
        changed = [c for c in diff(old, new) if c.attribute == 'required']
        assert changed
        assert all(c.directions == frozenset({'response'}) for c in changed)
        assert all(classify(c).name == 'response-property-required' for c in changed)
        assert RULES['response-property-required'].severity == 'safe'

    def test_the_same_edit_reads_the_other_way_in_a_response(self):
        """A property becoming *optional* is safe for a request and breaking for
        a response, where callers relied on it always being present.
        """
        assert RULES['response-property-optional'].severity == 'breaking'
        assert RULES['request-property-optional'].severity == 'safe'

    def test_a_tightened_bound_breaks_a_request(self, changes):
        found = changes(('maxLength: 32', 'maxLength: 16'))
        assert 'request-constraint-tightened' in found
        assert RULES['request-constraint-tightened'].severity == 'breaking'

    def test_a_loosened_bound_does_not(self, changes):
        found = changes(('maxLength: 32', 'maxLength: 64'))
        assert 'request-constraint-loosened' in found
        assert RULES['request-constraint-loosened'].severity == 'safe'

    def test_an_enum_value_added_to_a_response_is_risky_not_safe(self, changes):
        """A caller that switches exhaustively has no branch for it."""
        found = changes(('enum: [web, phone]', 'enum: [web, phone, app]'))
        assert 'response-enum-value-added' in found
        assert RULES['response-enum-value-added'].severity == 'risky'

    def test_a_removed_endpoint_is_breaking_whichever_side_you_are_on(self, changes):
        found = changes(drop=('/legacy:\n  get:\n    responses:\n      200:\n',))
        assert 'entity-removed' in found


#: Four named types, each carrying one constraint the numeric bounds do not cover,
#: all used as a request body so a tightening is breaking and a loosening is safe.
CONSTRAINTS = """#%RAML 1.0
title: T
types:
  Code:
    type: string
    pattern: '^[A-Z]+$'
  Count:
    type: number
    multipleOf: 2
  Tags:
    type: array
    items: string
    uniqueItems: true
  Filter:
    type: object
    additionalProperties: false
  Body:
    type: object
    properties:
      code: Code
      count: Count
      tags: Tags
      filter: Filter
/b:
  post:
    body:
      application/json: Body
"""


class TestNonNumericConstraints:
    """A pattern, `multipleOf`, `uniqueItems` and `additionalProperties` tighten or
    loosen exactly like a bound, so the direction that makes a bound breaking makes
    them so too. A swap neither can be ordered through is `other`, not a guess."""

    @staticmethod
    def graded(workspace, old: str, new: str) -> dict:
        root = workspace({'v1.raml': old, 'v2.raml': new})
        options = ParseOptions(unwrap=True)
        first = build_graph(parse_from_path(root / 'v1.raml', options))
        second = build_graph(parse_from_path(root / 'v2.raml', options))
        return {classify(c).name: c for c in diff(first, second)}

    @staticmethod
    def _scalar(kind: str, fmt: str | None) -> str:
        """A scalar type carrying a `format`, used as a request body."""
        facet = f'\n    format: {fmt}' if fmt else ''
        return f'#%RAML 1.0\ntitle: T\ntypes:\n  T:\n    type: {kind}{facet}\n/x:\n  post:\n    body:\n      application/json: T\n'

    def test_a_pattern_tightening_a_request_is_breaking(self, workspace):
        without = CONSTRAINTS.replace("    pattern: '^[A-Z]+$'\n", '')
        assert 'request-constraint-tightened' in self.graded(workspace, without, CONSTRAINTS)

    def test_a_pattern_loosening_a_request_is_safe(self, workspace):
        found = self.graded(workspace, CONSTRAINTS, CONSTRAINTS.replace("    pattern: '^[A-Z]+$'\n", ''))
        assert 'request-constraint-loosened' in found
        assert 'other' not in found

    def test_a_pattern_swap_is_not_orderable(self, workspace):
        swapped = CONSTRAINTS.replace("pattern: '^[A-Z]+$'", "pattern: '^[A-Z]0$'")
        assert 'other' in self.graded(workspace, CONSTRAINTS, swapped)

    def test_a_pattern_loosening_a_response_is_breaking(self, workspace):
        """The direction, not the constraint kind, is what decides it: the same
        loosening that is safe on a request breaks a client on a response."""
        base = (
            "#%RAML 1.0\ntitle: T\ntypes:\n  Code:\n    type: string\n    pattern: '^[A-Z]+$'\n"
            '  Body:\n    type: object\n    properties:\n      code: Code\n'
            '/b:\n  get:\n    responses:\n      200:\n        body:\n          application/json: Body\n'
        )
        found = self.graded(workspace, base, base.replace("    pattern: '^[A-Z]+$'\n", ''))
        assert 'response-constraint-loosened' in found
        assert RULES['response-constraint-loosened'].severity == 'breaking'

    def test_multiple_of_is_ordered_by_divisibility(self, workspace):
        two = CONSTRAINTS
        four = CONSTRAINTS.replace('multipleOf: 2', 'multipleOf: 4')
        three = CONSTRAINTS.replace('multipleOf: 2', 'multipleOf: 3')
        assert 'request-constraint-tightened' in self.graded(workspace, two, four)
        assert 'request-constraint-loosened' in self.graded(workspace, four, two)
        # `2` and `3` accept incommensurate sets, so neither loosens nor tightens.
        assert 'other' in self.graded(workspace, two, three)

    def test_unique_items_tightening_breaks_and_loosening_does_not(self, workspace):
        without = CONSTRAINTS.replace('    uniqueItems: true\n', '')
        assert 'request-constraint-tightened' in self.graded(workspace, without, CONSTRAINTS)
        assert 'request-constraint-loosened' in self.graded(workspace, CONSTRAINTS, without)

    def test_additional_properties_tightening_breaks_and_loosening_does_not(self, workspace):
        without = CONSTRAINTS.replace('    additionalProperties: false\n', '')
        assert 'request-constraint-tightened' in self.graded(workspace, without, CONSTRAINTS)
        assert 'request-constraint-loosened' in self.graded(workspace, CONSTRAINTS, without)

    def test_an_integer_format_is_ordered_by_width(self, workspace):
        """Widening an integer admits a superset of values, narrowing rejects some."""
        assert 'request-constraint-loosened' in self.graded(
            workspace, self._scalar('integer', 'int8'), self._scalar('integer', 'int64')
        )
        assert 'request-constraint-tightened' in self.graded(
            workspace, self._scalar('integer', 'int64'), self._scalar('integer', 'int8')
        )

    def test_a_number_format_is_ordered_by_width(self, workspace):
        assert 'request-constraint-loosened' in self.graded(
            workspace, self._scalar('number', 'float'), self._scalar('number', 'double')
        )
        assert 'request-constraint-tightened' in self.graded(
            workspace, self._scalar('number', 'double'), self._scalar('number', 'float')
        )

    def test_a_datetime_format_swap_is_breaking(self, workspace):
        """Its two formats are different wire spellings of the same instant, not a
        set one contains, so a move between them breaks a caller reading either —
        it is a representation change, not an unorderable swap."""
        found = self.graded(workspace, self._scalar('datetime', 'rfc3339'), self._scalar('datetime', 'rfc2616'))
        assert 'format-changed' in found
        assert RULES['format-changed'].severity == 'breaking'

    def test_a_datetime_escaping_its_default_is_breaking(self, workspace):
        """Absent is the RFC 3339 default, so choosing RFC 2616 changes the wire."""
        assert 'format-changed' in self.graded(
            workspace, self._scalar('datetime', None), self._scalar('datetime', 'rfc2616')
        )

    def test_writing_the_datetime_default_is_not_a_change(self, workspace):
        """`format: rfc3339` is what a `datetime` already is, so the wire is unchanged."""
        found = self.graded(workspace, self._scalar('datetime', None), self._scalar('datetime', 'rfc3339'))
        assert 'format-changed' not in found


class TestTheChangeList:
    def test_an_added_property_is_reported_once_per_site_not_once_per_node(self, changes):
        """Adding a property adds the node holding its type too. Reporting both
        says one thing twice and grades the consequence as an independent break.
        """
        found = changes(('      note?: string', '      note?: string\n      coupon?: string'))
        added = found.get('request-property-added', [])
        assert added
        assert not any(change.iri.endswith('/schema') for change in added)

    def test_differing_file_names_are_not_an_api_change(self, changes):
        """`name` on the unit node is the file name. Two versions are always in
        two files, so leaving it in reports every diff as changed.
        """
        assert changes() == {}

    def test_it_is_deterministic(self, workspace):
        root = workspace({'v1.raml': BASE, 'v2.raml': BASE.replace('maxLength: 32', 'maxLength: 16')})
        options = ParseOptions(unwrap=True)

        def once():
            old = build_graph(parse_from_path(root / 'v1.raml', options))
            new = build_graph(parse_from_path(root / 'v2.raml', options))
            return [(c.kind, c.iri, c.attribute) for c in diff(old, new)]

        assert once() == once()

    def test_a_document_against_itself_has_no_changes(self, workspace):
        root = workspace({'same.raml': BASE})
        graph = build_graph(parse_from_path(root / 'same.raml', ParseOptions(unwrap=True)))
        assert diff(graph, graph) == []


BOTH_WAYS = """#%RAML 1.0
title: T
types:
  Thing:
    type: object
    properties:
      a: string
/things:
  post:
    body:
      application/json: Thing
    responses:
      200:
        body:
          application/json: Thing
"""


class TestDirectionIsComputed:
    @staticmethod
    def both_ways(workspace, edit: tuple[str, str]):
        root = workspace({'v1.raml': BOTH_WAYS, 'v2.raml': BOTH_WAYS.replace(*edit)})
        options = ParseOptions(unwrap=True)
        old = build_graph(parse_from_path(root / 'v1.raml', options))
        new = build_graph(parse_from_path(root / 'v2.raml', options))
        return next(c for c in diff(old, new) if '#/declarations/' in c.iri and c.attribute == 'required')

    def test_both_sides_are_recognised(self, changes):
        found = changes(
            ('      note?: string', '      note?: string\n      coupon?: string'),
            ('      discount: number', '      discount: number\n      refund?: number'),
        )
        sides = {side for members in found.values() for change in members for side in change.directions}
        assert {'request', 'response'} <= sides

    def test_a_type_used_both_ways_carries_both_sides(self, workspace):
        """The canonical CRUD shape: one type as a POST body and a GET response.

        `_sides` used to stop at the first side it reached, so this was graded
        by whichever edge came off the stack first — not merely wrong, unstable.
        """
        change = self.both_ways(workspace, ('      a: string', '      a?: string'))
        assert change.directions == frozenset({'request', 'response'})

    def test_the_worse_side_decides(self, workspace):
        """Required becoming optional is safe for a request and breaking for a
        response. A type that is both must report breaking, or the reassurance
        buries the break.
        """
        change = self.both_ways(workspace, ('      a: string', '      a?: string'))
        assert classify(change).severity == 'breaking'


class TestTheSideMapIsAForwardPropagation:
    """docs/12 § 19f: the side of the wire is carried down, not computed up.

    This replaced a reverse walk *per node* — 36 510 independent traversals on
    `bench_endpoints`, re-deriving the same ancestor chains — with one worklist
    over the edges. The fixpoint has to mean exactly what the walk meant, so
    the walk is kept here as the specification and the two are compared.
    """

    @staticmethod
    def by_walking(graph, iri: str) -> frozenset[str]:
        """The original: every side reachable by walking back towards the API."""
        found: set[str] = set()
        seen: set[str] = set()
        frontier = [iri]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            for edge in graph.into(current):
                if edge.predicate == 'request':
                    found.add('request')
                elif edge.predicate == 'returns':
                    found.add('response')
                frontier.append(edge.subject)
        return frozenset(found) or frozenset({'declaration'})

    def test_it_agrees_with_the_walk_it_replaced(self, workspace):
        root = workspace({'v1.raml': BOTH_WAYS})
        graph = build_graph(parse_from_path(root / 'v1.raml', ParseOptions(unwrap=True)))
        computed = _side_map(graph)
        assert {iri: computed[iri] for iri in graph.nodes} == {iri: self.by_walking(graph, iri) for iri in graph.nodes}

    def test_a_cycle_settles_rather_than_running_away(self, workspace):
        """A recursive type closes a cycle in the graph. A fixpoint over a
        four-state lattice stops widening; a walk needed its own `seen` set."""
        source = BOTH_WAYS.replace('      a: string', '      a: string\n      self?: Thing')
        root = workspace({'v1.raml': source})
        graph = build_graph(parse_from_path(root / 'v1.raml', ParseOptions(unwrap=True)))
        computed = _side_map(graph)
        assert {iri: computed[iri] for iri in graph.nodes} == {iri: self.by_walking(graph, iri) for iri in graph.nodes}

    def test_every_node_is_labelled_including_unreachable_ones(self, workspace):
        """Seeded with every node, so a declaration nothing references still
        gets an answer rather than a `KeyError` in `diff`."""
        root = workspace({'v1.raml': BOTH_WAYS})
        graph = build_graph(parse_from_path(root / 'v1.raml', ParseOptions(unwrap=True)))
        computed = _side_map(graph)
        assert set(computed) >= set(graph.nodes)
        assert all(value for value in computed.values())


SECURED = """#%RAML 1.0
title: T
securitySchemes:
  oauth:
    type: OAuth 2.0
    settings:
      authorizationUri: https://e.test/a
      accessTokenUri: https://e.test/t
      authorizationGrants: [authorization_code]
  apiKey:
    type: Pass Through
    describedBy:
      headers:
        X-Key: string
types:
  Ref: string
  Other: string
/things:
  get:
    securedBy: [oauth]
    responses:
      200:
        body:
          application/json: Ref
"""


class TestReferencesAreDiffedToo:
    """Edges, not only nodes and attributes.

    A reference can change while every node stays exactly where it was. Swapping
    an operation's `securedBy` from OAuth 2.0 to an API key alters no node and no
    attribute — and the first version of this reported no changes at all and
    exited 0, which for a tool whose contract is "exit 1 if breaking" is the
    worst answer available.
    """

    @staticmethod
    def graded(workspace, replacements: list[tuple[str, str]], base: str = SECURED) -> dict:
        after = base
        for old, new in replacements:
            assert old in after, old
            after = after.replace(old, new)
        root = workspace({'v1.raml': base, 'v2.raml': after})
        options = ParseOptions(unwrap=True)
        old_graph = build_graph(parse_from_path(root / 'v1.raml', options))
        new_graph = build_graph(parse_from_path(root / 'v2.raml', options))
        return {classify(change).name: change for change in diff(old_graph, new_graph)}

    def test_a_swapped_security_scheme_is_breaking(self, workspace):
        found = self.graded(workspace, [('securedBy: [oauth]', 'securedBy: [apiKey]')])
        assert 'security-added' in found
        assert RULES['security-added'].severity == 'breaking'

    def test_requiring_no_credential_where_one_was_required_is_safe(self, workspace):
        found = self.graded(workspace, [('    securedBy: [oauth]\n', '')])
        assert 'security-removed' in found
        assert RULES['security-removed'].severity == 'safe'
        assert 'security-added' not in found

    def test_unsecuring_a_method_reads_safe_not_risky(self, workspace):
        """`securedBy: [null]` also flips the method's `unsecured` attribute. Graded
        as a security change it reads safe, the mirror of `security-removed`; left to
        `other` it over-graded an unsecured method as risky."""
        found = self.graded(workspace, [('securedBy: [oauth]', 'securedBy: [null]')])
        assert 'security-removed' in found
        assert 'other' not in found
        assert RULES['security-removed'].severity == 'safe'

    def test_resecuring_a_method_is_breaking(self, workspace):
        """The mirror: the `unsecured` attribute vanishes the moment a credential is
        required, and refusing an unauthenticated caller is breaking."""
        unsecured = SECURED.replace('securedBy: [oauth]', 'securedBy: [null]')
        found = self.graded(workspace, [('securedBy: [null]', 'securedBy: [oauth]')], base=unsecured)
        assert 'security-added' in found
        assert 'other' not in found
        assert RULES['security-added'].severity == 'breaking'

    def test_a_retargeted_reference_is_reported(self, workspace):
        """`Ref` and `Other` are both `string`, so no node and no attribute
        differ — only the edge naming which one the body uses.
        """
        found = self.graded(workspace, [('application/json: Ref', 'application/json: Other')])
        assert 'reference-retargeted' in found
        assert found['reference-retargeted'].attribute == 'aliasOf'

    def test_a_swap_arrives_as_a_drop_and_an_arrival(self, workspace):
        """Not as an opaque "changed": which target went and which came is what
        decides whether the swap breaks anyone.
        """
        found = self.graded(workspace, [('securedBy: [oauth]', 'securedBy: [apiKey]')])
        assert found['security-removed'].kind == 'unlinked'
        assert found['security-added'].kind == 'linked'

    def test_containment_edges_are_not_diffed(self, workspace):
        """They cannot change without the node at the end being added or
        removed, so diffing them would repeat what the node already said.
        """
        found = self.graded(workspace, [('  Other: string\n', '')])
        assert not any(change.attribute in ('property', 'payload', 'returns') for change in found.values())


class TestRecordSeparatesThePropertyFromTheDelta:
    """`--json` is the regrading contract, so every fact a rule uses must be in the
    record. `required` appears twice — as an `attribute` *value* (a delta on a
    changed node) and as the node's own facet (a property of an added/removed
    node) — and the two must not be conflated."""

    BASE = (
        '#%RAML 1.0\ntitle: T\ntypes:\n  N:\n    type: object\n    properties:\n'
        '      a?: string\n/b:\n  post:\n    body:\n      application/json: N\n'
    )

    @staticmethod
    def _record(workspace, v1: str, v2: str, pick) -> dict:
        root = workspace({'a.raml': v1, 'b.raml': v2})
        options = ParseOptions(unwrap=True)
        old = build_graph(parse_from_path(root / 'a.raml', options))
        new = build_graph(parse_from_path(root / 'b.raml', options))
        change = next(c for c in diff(old, new) if pick(c))
        return record(classify(change), change)

    def test_an_added_required_property_is_a_property_not_a_delta(self, workspace):
        rec = self._record(
            workspace,
            self.BASE,
            self.BASE.replace('      a?: string', '      a?: string\n      b: string'),
            lambda c: c.kind == 'added',
        )
        assert rec['attribute'] is None
        assert rec['required'] is True

    def test_a_required_flip_is_a_delta_not_the_property(self, workspace):
        rec = self._record(
            workspace,
            self.BASE,
            self.BASE.replace('      a?: string', '      a: string'),
            lambda c: c.kind == 'changed',
        )
        assert rec['attribute'] == 'required'
        assert (rec['before'], rec['after']) == (False, True)
        assert rec['required'] is None

    def test_the_property_is_null_where_it_does_not_apply(self, workspace):
        base = '#%RAML 1.0\ntitle: T\n/b:\n  post:\n    responses:\n      200:\n'
        rec = self._record(workspace, base, base + '      404:\n', lambda c: c.kind == 'added')
        assert rec['node_kind'] == 'Response'
        assert rec['required'] is None
