"""What changed, and what it breaks — docs/16-graph.md § 10.

The change list is mechanical and easy to check. The classification is not, and
almost all of these are about it, because **the same edit is breaking on one
side of the wire and harmless on the other**. A rule table that got the
direction backwards would still produce confident, well-formatted output.
"""

from __future__ import annotations

import pytest

from pyraml import ParseOptions, parse_from_path
from pyraml.views.diff import RULES, classify, diff
from pyraml.views.graph import build_graph

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
    def graded(workspace, replacements: list[tuple[str, str]]):
        after = SECURED
        for old, new in replacements:
            assert old in after, old
            after = after.replace(old, new)
        root = workspace({'v1.raml': SECURED, 'v2.raml': after})
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
