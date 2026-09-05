"""What changed between two versions, and whether it breaks anyone —
docs/16-graph.md § 10.

Two things, kept apart on purpose.

`diff` produces a **change list**: added, removed and altered nodes, each with
the IRI that identifies it in both versions. It holds no opinion. This is the
contract, and a consumer that disagrees with everything below can work from it
alone through `--json`.

`classify` applies the one opinion worth shipping: **backward compatibility**.
That is not org-specific policy in the way "every operation must be documented"
is (docs/16 § 7's line, which still stands) — it follows from the spec's own
semantics, from `required` and from which side of the wire a node sits on.

The diff is cheap because the IRIs are structural (§ 3): the same entity has the
same name in both versions, so matching is a dict lookup rather than a
similarity search. That property was designed for the declared-versus-effective
case and pays here for nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyraml.graph import Graph, GraphNode

__all__ = ['Change', 'Direction', 'Severity', 'classify', 'diff']

Direction = Literal['request', 'response', 'declaration']
Severity = Literal['breaking', 'risky', 'safe', 'cosmetic']

#: Attributes that say where something was written, not what it means. A moved
#: line is not a change to the API.
_POSITIONAL: Final = frozenset({'column', 'definedIn', 'line'})

#: Attributes nobody consumes at runtime. Reported, graded `cosmetic`.
_PROSE: Final = frozenset({'description', 'displayName', 'usage'})

#: Edges that name something declared elsewhere, rather than containing it.
#: These are the only ones worth diffing: a containment edge cannot change
#: without the node at its end being added or removed, so diffing it repeats
#: what the node already said. A **reference** can change while every node stays
#: exactly where it was — swapping an operation's `securedBy` from one scheme to
#: another alters no node and no attribute, and went unreported until a test
#: swapped OAuth 2.0 for an API key and this said the document was unchanged.
_REFERENCE_EDGES: Final = frozenset(
    {'aliasOf', 'annotation', 'appliesResourceType', 'appliesTrait', 'inherits', 'recursionHead', 'securedBy'}
)

#: Derived, never independently meaningful. A node's `name` is either part of
#: its own IRI — so a rename arrives as a removal and an addition, not as an
#: attribute change — or it is inherited from its owner. Left in, it reports
#: the *file names* differing between the two versions as an API change, and
#: reports `note? -> note` a second time alongside the `required` change that
#: actually says it.
_DERIVED: Final = frozenset({'name'})


@dataclass(frozen=True, slots=True)
class Change:
    """One difference, with enough context to grade it without re-deriving any.

    `directions` is the part that cannot be recovered later. Whether a property
    became required is breaking or harmless depends entirely on which side of
    the wire consumes it, and only the graph's containment path knows.

    A **set**, because one declared type is routinely both: `Order` is a POST
    body and a GET response in the same document. Reporting only the first side
    found grades the canonical CRUD shape by whichever edge the walk reached
    first, which is not even stable, let alone right.
    """

    kind: Literal['added', 'removed', 'changed', 'linked', 'unlinked']
    iri: str
    node_kind: str
    directions: frozenset[Direction]
    #: The attribute for `changed`, or the predicate for `linked`/`unlinked`.
    attribute: str | None = None
    before: object = None
    after: object = None

    @property
    def direction(self) -> Direction:
        """One side, for display. Request first: it is the stricter reading."""
        if 'request' in self.directions:
            return 'request'
        return 'response' if 'response' in self.directions else 'declaration'

    def __repr__(self) -> str:
        detail = f' {self.attribute}' if self.attribute else ''
        return f'Change({self.kind}{detail} {self.node_kind} {self.iri!r})'


# -- the change list ----------------------------------------------------------


def diff(old: Graph, new: Graph) -> list[Change]:
    """Every difference between two projections, in the old graph's order.

    Deterministic: removals and alterations follow the order the old document
    declared them, then additions follow the new one's. A diff whose order
    varied could not be committed or compared.
    """
    changes: list[Change] = []
    for iri, before in old.nodes.items():
        after = new.nodes.get(iri)
        if after is None:
            changes.append(Change(kind='removed', iri=iri, node_kind=before.kinds[0], directions=_sides(old, iri)))
        else:
            changes.extend(_altered(iri, before, after, _sides(new, iri)))
    changes.extend(
        Change(kind='added', iri=iri, node_kind=node.kinds[0], directions=_sides(new, iri))
        for iri, node in new.nodes.items()
        if iri not in old.nodes
    )
    changes.extend(_relinked(old, new))
    return _without_subsumed(changes)


def _relinked(old: Graph, new: Graph) -> Iterator[Change]:
    """References that now point somewhere else, or nowhere.

    Compared per (subject, predicate) so a swap arrives as one `unlinked` and
    one `linked` rather than as an opaque "changed": which target went and which
    arrived is exactly what decides whether the swap is breaking.
    """
    before, after = _references(old), _references(new)
    for key in sorted(before.keys() | after.keys()):
        iri, predicate = key
        was, now = before.get(key, frozenset()), after.get(key, frozenset())
        node = new.nodes.get(iri) or old.nodes.get(iri)
        graph = new if iri in new.nodes else old
        for gone in sorted(was - now):
            yield Change(
                kind='unlinked',
                iri=iri,
                node_kind=node.kinds[0] if node else 'Unknown',
                directions=_sides(graph, iri),
                attribute=predicate,
                before=gone,
            )
        for arrived in sorted(now - was):
            yield Change(
                kind='linked',
                iri=iri,
                node_kind=node.kinds[0] if node else 'Unknown',
                directions=_sides(graph, iri),
                attribute=predicate,
                after=arrived,
            )


def _references(graph: Graph) -> dict[tuple[str, str], frozenset[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for edge in graph.edges:
        if edge.predicate in _REFERENCE_EDGES:
            found.setdefault((edge.subject, edge.predicate), set()).add(edge.object)
    return {key: frozenset(targets) for key, targets in found.items()}


def _without_subsumed(changes: list[Change]) -> list[Change]:
    """Drop what a coarser change already said.

    Removing a property removes the node holding its type, and adding one adds
    it; reporting both says one thing twice and grades the consequence as if it
    were an independent break. A change strictly *inside* something that was
    itself added or removed is subsumed by it.
    """
    structural = sorted(
        (change.iri for change in changes if change.kind in ('added', 'removed')),
        key=len,
    )
    return [
        change
        for change in changes
        if not any(change.iri != outer and change.iri.startswith(outer + '/') for outer in structural)
    ]


def _altered(iri: str, before: GraphNode, after: GraphNode, directions: frozenset[Direction]) -> Iterator[Change]:
    for key in sorted(before.attributes.keys() | after.attributes.keys()):
        if key in _POSITIONAL or key in _DERIVED:
            continue
        was, now = before.attributes.get(key), after.attributes.get(key)
        if was != now:
            yield Change(
                kind='changed',
                iri=iri,
                node_kind=after.kinds[0],
                directions=directions,
                attribute=key,
                before=was,
                after=now,
            )


def _sides(graph: Graph, iri: str) -> frozenset[Direction]:
    """Every side of the wire this node reaches, walking back towards the API.

    Walked over edges rather than read off the IRI: the IRI happens to encode
    the same thing today, and an edge is what the projection actually promises.

    The walk does **not** stop at the first side it finds. A declared type is
    commonly a request body and a response body at once, and stopping early
    grades it by whichever edge came off the stack first.
    """
    found: set[Direction] = set()
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


# -- the policy ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """One named judgement. Named so it can be cited, and suppressed, by name."""

    name: str
    severity: Severity
    because: str


_REMOVED_ENTITY: Final = frozenset({'EndPoint', 'Operation', 'Response'})

#: Every rule `classify` can return, so a consumer can enumerate them without
#: reading the function. docs/16 § 10.2 is this table in prose.
RULES: Final[dict[str, Rule]] = {
    rule.name: rule
    for rule in (
        Rule('entity-removed', 'breaking', 'a resource, method or status code callers may still be using'),
        Rule('entity-added', 'safe', 'new surface; nothing that worked stops working'),
        Rule('request-property-required', 'breaking', 'a request that omitted it is now rejected'),
        Rule('request-property-optional', 'safe', 'a request that supplied it still works'),
        Rule('request-property-added', 'safe', 'optional by construction until it is required'),
        Rule('request-property-removed', 'risky', 'the server stops reading a value callers still send'),
        Rule('request-constraint-tightened', 'breaking', 'a value that was accepted is now rejected'),
        Rule('request-constraint-loosened', 'safe', 'strictly more input is accepted'),
        Rule('request-enum-value-removed', 'breaking', 'callers may still send it'),
        Rule('request-enum-value-added', 'safe', 'the server accepts more than it did'),
        Rule('response-property-removed', 'breaking', 'callers read a field that has gone'),
        Rule('response-property-optional', 'breaking', 'callers relied on it always being present'),
        Rule('response-property-added', 'safe', 'a caller that ignores unknown fields is unaffected'),
        Rule('response-constraint-loosened', 'breaking', 'a value arrives that callers cannot handle'),
        Rule('response-constraint-tightened', 'safe', 'strictly less variety arrives'),
        Rule('response-enum-value-added', 'risky', 'a caller that switches exhaustively has no branch for it'),
        Rule('response-enum-value-removed', 'safe', 'one fewer case to handle'),
        Rule('security-added', 'breaking', 'an unauthenticated caller is now refused'),
        Rule('security-removed', 'safe', 'a credential that was required is merely ignored'),
        Rule('type-changed', 'breaking', 'the wire format is not the one either side agreed'),
        Rule('documentation-changed', 'cosmetic', 'nothing on the wire changed'),
        Rule('reference-retargeted', 'risky', 'it now names something else; the two may not agree'),
        Rule('reference-dropped', 'risky', 'it no longer names what it did'),
        Rule('other', 'risky', 'not covered by a rule; read it yourself'),
    )
}

#: Facets whose *larger* value is the more permissive one. `maxLength: 8 -> 16`
#: accepts more; `minLength: 2 -> 4` accepts less. Which of those breaks a
#: caller then depends on the direction, which is the whole point of § 10.2.
_UPPER_BOUNDS: Final = frozenset({'maxItems', 'maxLength', 'maxProperties', 'maximum'})
_LOWER_BOUNDS: Final = frozenset({'minItems', 'minLength', 'minProperties', 'minimum'})


#: Worst first. A change reaching both sides of the wire is reported at the
#: severity of the worse one; anything else buries a break under a reassurance.
_ORDER: Final = ('breaking', 'risky', 'safe', 'cosmetic')


def classify(change: Change) -> Rule:
    """The backward-compatibility judgement for one change.

    Returns the `Rule`, not a bare severity, so a caller can report *why* and
    can suppress by name. `other` is deliberate: an unrecognised change is
    `risky` rather than `safe`, because silence about something unclassified is
    the one answer that misleads.

    Graded once per side the change reaches, worst reported.
    """
    graded = [_rule_for(change, side) for side in sorted(change.directions)]
    return min(graded, key=lambda rule: _ORDER.index(rule.severity))


def _rule_for(change: Change, direction: Direction) -> Rule:
    if change.kind in ('linked', 'unlinked'):
        return RULES[_link_rule(change)]
    if change.kind == 'removed':
        return RULES['entity-removed' if change.node_kind in _REMOVED_ENTITY else _removed_rule(change, direction)]
    if change.kind == 'added':
        return RULES[_added_rule(change, direction)]
    return RULES[_changed_rule(change, direction)]


def _link_rule(change: Change) -> str:
    """A reference gained or lost.

    `securedBy` is graded outright: requiring a credential where none was
    required refuses every existing caller, and dropping one refuses nobody.
    Everything else is `risky` — an inheritance or annotation now naming
    something different is a real change whose effect this cannot compute.
    """
    if change.attribute == 'securedBy':
        return 'security-added' if change.kind == 'linked' else 'security-removed'
    return 'reference-retargeted' if change.kind == 'linked' else 'reference-dropped'


def _removed_rule(change: Change, direction: Direction) -> str:
    if change.node_kind in ('Property', 'Parameter') and direction != 'declaration':
        return f'{direction}-property-removed'
    return 'entity-removed'


def _added_rule(change: Change, direction: Direction) -> str:
    if change.node_kind in ('Property', 'Parameter') and direction != 'declaration':
        return f'{direction}-property-added'
    return 'entity-added'


def _changed_rule(change: Change, direction: Direction) -> str:  # noqa: PLR0911 - one arm per attribute family
    attribute, was, now = change.attribute, change.before, change.after
    if attribute in _PROSE:
        return 'documentation-changed'
    if attribute == 'required' and direction != 'declaration':
        became = 'required' if now else 'optional'
        return f'{direction}-property-{became}'
    if attribute == 'enum':
        return _enum_rule(change, direction)
    if attribute in ('type', 'kind', 'mediaType', 'statusCode'):
        return 'type-changed'
    if attribute == 'scopes' or change.node_kind == 'SecurityScheme':
        return 'security-added' if not was and now else 'security-removed'
    if attribute in _UPPER_BOUNDS or attribute in _LOWER_BOUNDS:
        return _bound_rule(change, direction)
    return 'other'


def _enum_rule(change: Change, direction: Direction) -> str:
    was = set(change.before if isinstance(change.before, tuple) else ())
    now = set(change.after if isinstance(change.after, tuple) else ())
    if direction == 'declaration':
        return 'other'
    if was - now:
        return f'{direction}-enum-value-removed'
    if now - was:
        return f'{direction}-enum-value-added'
    return 'other'


def _bound_rule(change: Change, direction: Direction) -> str:
    """Whether a numeric bound was loosened or tightened, and for whom."""
    loosened = _loosened(change.attribute or '', change.before, change.after)
    if loosened is None or direction == 'declaration':
        return 'other'
    return f'{direction}-constraint-{"loosened" if loosened else "tightened"}'


def _loosened(attribute: str, before: object, after: object) -> bool | None:
    """`True` if the bound now permits more, `None` if it cannot be compared.

    A bound appearing or disappearing counts: removing `maxLength` permits
    everything, and adding one permits less than before.
    """
    was, now = _numeric(before), _numeric(after)
    if was is None and now is None:
        return None
    if was is None:
        return False  # a bound where there was none is always a tightening
    if now is None:
        return True
    if attribute in _UPPER_BOUNDS:
        return now > was
    return now < was


def _numeric(value: object) -> float | None:
    """A bound as a number, or `None`.

    The one place a `float` is acceptable in this project: nothing here is
    compared against a *value*, only two bounds against each other to decide
    which way they moved (docs/10 § 5.2 is about validation, not about this).
    Exactness would change no answer, and a bound too large for a float is
    already not a bound anyone is enforcing.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None
