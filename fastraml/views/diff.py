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

from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Final, Literal

from fastraml.types.scalars import DATETIME_FORMATS, INTEGER_FORMATS
from fastraml.views.severity import Ranking

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from fastraml.views.graph import Graph, GraphNode

__all__ = ['Change', 'Direction', 'Severity', 'at_least', 'classify', 'diff', 'plain', 'record', 'worst']

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
    #: A node property, not a difference: whether an added or removed Property or
    #: Parameter was required. It has no `before`/`after`, because the node itself
    #: is the change — this is the `required` facet of that node, distinct from the
    #: `required` that appears as an `attribute` *value* on a `changed` node. It is
    #: carried here (and is `None` elsewhere) because `classify` grades from the
    #: `Change` alone and `record` must let a consumer regrade from `--json` without
    #: the model; for an added node there is no delta to carry it in.
    required: bool | None = None

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
    # Once per graph, not once per node (`_side_map`). A removed node's side has
    # to come from the graph that still has it, which is why both are built.
    was, now = _side_map(old), _side_map(new)
    changes: list[Change] = []
    for iri, before in old.nodes.items():
        after = new.nodes.get(iri)
        if after is None:
            changes.append(
                Change(
                    kind='removed',
                    iri=iri,
                    node_kind=before.kinds[0],
                    directions=was[iri],
                    required=_required_of(before),
                )
            )
        else:
            changes.extend(_altered(iri, before, after, now[iri]))
    changes.extend(
        Change(kind='added', iri=iri, node_kind=node.kinds[0], directions=now[iri], required=_required_of(node))
        for iri, node in new.nodes.items()
        if iri not in old.nodes
    )
    changes.extend(_relinked(old, new, was, now))
    return _without_subsumed(changes)


def _required_of(node: GraphNode) -> bool | None:
    """Whether an added or removed node was a required property or parameter.

    Those are the only two kinds whose `required` changes a grade, and it is read
    off the entity, which is one attribute: building the node's whole `attributes`
    dictionary to ask one question is the cost docs/12 § 19e warns against.
    """
    if node.kinds[0] in ('Property', 'Parameter'):
        return bool(node.entity.required)
    return None


def _relinked(
    old: Graph,
    new: Graph,
    old_sides: Mapping[str, frozenset[Direction]],
    new_sides: Mapping[str, frozenset[Direction]],
) -> Iterator[Change]:
    """References that now point somewhere else, or nowhere.

    Compared per (subject, predicate) so a swap arrives as one `unlinked` and
    one `linked` rather than as an opaque "changed": which target went and which
    arrived is exactly what decides whether the swap is breaking.

    The side maps are passed in rather than rebuilt: these are the same two
    graphs `diff` has already labelled, and a subject here is a node there.
    """
    before, after = _references(old), _references(new)
    for key in sorted(before.keys() | after.keys()):
        iri, predicate = key
        was, now = before.get(key, frozenset()), after.get(key, frozenset())
        node = new.nodes.get(iri) or old.nodes.get(iri)
        # The graph that still holds the subject decides its side, and a subject
        # present in neither is graded as a declaration rather than dropped.
        sides = new_sides.get(iri) or old_sides.get(iri) or _DECLARATION
        for gone in sorted(was - now):
            yield Change(
                kind='unlinked',
                iri=iri,
                node_kind=node.kinds[0] if node else 'Unknown',
                directions=sides,
                attribute=predicate,
                before=gone,
            )
        for arrived in sorted(now - was):
            yield Change(
                kind='linked',
                iri=iri,
                node_kind=node.kinds[0] if node else 'Unknown',
                directions=sides,
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
    # Bound once each: `attributes` is derived from the entity per read, so
    # asking four times per node pair would build four dictionaries
    # (docs/16 § 2.8).
    was_all, now_all = before.attributes, after.attributes
    for key in sorted(was_all.keys() | now_all.keys()):
        if key in _POSITIONAL or key in _DERIVED:
            continue
        was, now = was_all.get(key), now_all.get(key)
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


#: The two edges that say which side of the wire everything beneath them is on,
#: as bits. Nothing else changes the answer, so propagation watches only these.
#:
#: A bitmask rather than a set because this is the inner loop of `_side_map`:
#: `carried | bit` allocates nothing, where `carried | {side}` builds two sets
#: per edge and costs more than the per-node walk the pass replaces — 71.5 ms
#: against 62.5 ms on `bench_endpoints` (docs/12 § 19f).
_REQUEST: Final = 1
_RESPONSE: Final = 2
_BIT: Final[dict[str, int]] = {'request': _REQUEST, 'returns': _RESPONSE}

#: What a node reached by no side edge is. Shared rather than rebuilt per node:
#: on a type-only document this is the answer for every node in the graph.
_DECLARATION: Final[frozenset[Direction]] = frozenset({'declaration'})

#: The four states the lattice has, interned and indexed by mask. Labelling a
#: graph then costs no allocation at all — every node is handed one of these
#: four objects.
_SIDES: Final[tuple[frozenset[Direction], ...]] = (
    _DECLARATION,
    frozenset({'request'}),
    frozenset({'response'}),
    frozenset({'request', 'response'}),
)


def _side_map(graph: Graph) -> dict[str, frozenset[Direction]]:
    """Every node's side of the wire, for the whole graph, in one pass.

    **The side is carried down, not computed up.** Which side a node sits on is
    a fact about its ancestors — containment points downwards, from an operation
    through its request or its responses to a payload, a schema and a property —
    so the obvious implementation asks each node to walk back towards the API.
    That is what this did, once per node, and it was 41% of a diff: 36 510
    independent reverse traversals, each allocating its own frontier and seen
    set, re-walking the same ancestor chains endlessly. Every property of `User`
    re-derived `User`'s entire ancestry from scratch.

    It is a union over parents, which makes it a forward propagation:

        sides(n) = union over incoming e of ( side_of(e.predicate) | sides(e.subject) )

    so one worklist over the edges settles every node at once. The lattice is
    four states — neither, request, response, both — so a node is re-enqueued at
    most twice and the fixpoint is reached in O(E). Cycles need no special case;
    they simply stop widening.

    A node keeps **every** side that reaches it. A declared type is routinely a
    request body and a response body in the same document, and grading it by
    whichever path arrived first is not even deterministic, let alone right.
    """
    # Seeded with every node, so one that no edge reaches still gets an answer.
    mask: dict[str, int] = dict.fromkeys(graph.nodes, 0)
    queue: deque[str] = deque(graph.nodes)
    bits = _BIT
    out = graph.out

    while queue:
        iri = queue.popleft()
        carried = mask.get(iri, 0)
        for edge in out(iri):
            reaching = carried | bits.get(edge.predicate, 0)
            if not reaching:
                # Nothing to hand down. Most edges on most documents take this
                # branch, which is why the seed can be every node rather than
                # only the roots — a node with nothing to give costs one lookup.
                continue
            target = edge.object
            current = mask.get(target, 0)
            if reaching & ~current:
                mask[target] = current | reaching
                queue.append(target)

    return {iri: _SIDES[found] for iri, found in mask.items()}


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
        Rule('request-property-added-required', 'breaking', 'a request that omits the new field is now rejected'),
        Rule('request-property-added', 'safe', 'a new optional field; a request that omits it still works'),
        Rule('request-property-removed', 'risky', 'the server stops reading a value callers still send'),
        Rule('request-constraint-tightened', 'breaking', 'a value that was accepted is now rejected'),
        Rule('request-constraint-loosened', 'safe', 'strictly more input is accepted'),
        Rule('request-enum-value-removed', 'breaking', 'callers may still send it'),
        Rule('request-enum-value-added', 'safe', 'the server accepts more than it did'),
        Rule('response-property-removed', 'breaking', 'callers read a field that has gone'),
        Rule('response-property-optional', 'breaking', 'callers relied on it always being present'),
        Rule(
            'response-property-required', 'safe', 'a field callers already accept as absent is now guaranteed present'
        ),
        Rule('response-property-added', 'safe', 'a caller that ignores unknown fields is unaffected'),
        Rule('response-constraint-loosened', 'breaking', 'a value arrives that callers cannot handle'),
        Rule('response-constraint-tightened', 'safe', 'strictly less variety arrives'),
        Rule('response-enum-value-added', 'risky', 'a caller that switches exhaustively has no branch for it'),
        Rule('response-enum-value-removed', 'safe', 'one fewer case to handle'),
        Rule('security-added', 'breaking', 'an unauthenticated caller is now refused'),
        Rule('security-removed', 'safe', 'a credential that was required is merely ignored'),
        Rule('type-changed', 'breaking', 'the wire format is not the one either side agreed'),
        Rule('format-changed', 'breaking', 'the value is now spelled in a representation callers do not parse'),
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

#: Constraints that tighten or loosen without being an ordered bound. A `pattern`,
#: `format` or `multipleOf` appearing is a tightening and one vanishing a
#: loosening; `uniqueItems` and `additionalProperties` are decided by their value;
#: and a swap of two incomparable values is `other`, because nothing here can
#: order two different patterns or formats.
_RESTRICTING: Final = frozenset({'pattern', 'format', 'multipleOf', 'uniqueItems', 'additionalProperties'})


#: Worst first. A change reaching both sides of the wire is reported at the
#: severity of the worse one; anything else buries a break under a reassurance.
#: The arithmetic is shared with `lint`, which grades on a different axis with
#: the same operations (`views/severity.py`).
_ORDER: Final[Ranking[Severity]] = Ranking(('breaking', 'risky', 'safe', 'cosmetic'))


def classify(change: Change) -> Rule:
    """The backward-compatibility judgement for one change.

    Returns the `Rule`, not a bare severity, so a caller can report *why* and
    can suppress by name. `other` is deliberate: an unrecognised change is
    `risky` rather than `safe`, because silence about something unclassified is
    the one answer that misleads.

    Graded once per side the change reaches, worst reported.
    """
    graded = [_rule_for(change, side) for side in sorted(change.directions)]
    return min(graded, key=lambda rule: _ORDER.rank(rule.severity))


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
        # A new request field is breaking only when it is required: omitting it is
        # then rejected. A new response field is safe whether or not it is required,
        # so only the request side splits.
        if direction == 'request' and change.required:
            return 'request-property-added-required'
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
    # A `datetime`'s two formats are different wire spellings of the same instant,
    # not a set one contains, so moving between them — absent being the RFC 3339
    # default — breaks a caller reading either. That is a change of representation,
    # not a loosening, so it is graded as such rather than run through the bounds.
    if attribute == 'format' and (was in DATETIME_FORMATS or now in DATETIME_FORMATS):
        return 'format-changed' if _datetime_format_changed(was, now) else 'documentation-changed'
    if attribute == 'scopes' or change.node_kind == 'SecurityScheme':
        return 'security-added' if not was and now else 'security-removed'
    # `unsecured` is `securedBy: [null]` seen from the method's own side: it appears
    # when security is dropped (safe) and vanishes when it is required (breaking), so
    # it is graded as a security change. Left to `other`, unsecuring a method would
    # over-grade as risky instead of the safe mirror of `security-removed`.
    if attribute == 'unsecured':
        return 'security-added' if not now else 'security-removed'
    if attribute in _UPPER_BOUNDS or attribute in _LOWER_BOUNDS or attribute in _RESTRICTING:
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
    """Whether a bound or constraint was loosened or tightened, and for whom."""
    loosened = _loosened(change.attribute or '', change.before, change.after)
    if loosened is None or direction == 'declaration':
        return 'other'
    return f'{direction}-constraint-{"loosened" if loosened else "tightened"}'


def _loosened(attribute: str, before: object, after: object) -> bool | None:
    """`True` if it now permits more, `False` if less, `None` if it cannot be compared.

    A bound or constraint appearing or disappearing counts: removing `maxLength`
    permits everything, and adding one permits less than before.
    """
    if attribute in _RESTRICTING:
        return _restricting_loosened(attribute, before, after)
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


def _restricting_loosened(attribute: str, before: object, after: object) -> bool | None:
    """Loosen or tighten for a constraint that is not an ordered numeric bound."""
    if attribute in ('uniqueItems', 'additionalProperties'):
        return _flag_loosened(before, after, restrictive=attribute == 'uniqueItems')
    if before is None:
        return None if after is None else False  # a constraint where there was none
    if after is None:
        return True  # a constraint lifted
    if attribute == 'multipleOf':
        return _multiple_of_loosened(before, after)
    if attribute == 'format':
        return _format_loosened(before, after)
    return None  # two patterns: nothing here can order them


def _flag_loosened(before: object, after: object, *, restrictive: bool) -> bool | None:
    """`uniqueItems` and `additionalProperties` as flags.

    `restrictive` names the value that tightens: `uniqueItems: true` rejects
    duplicates, `additionalProperties: false` rejects extras. `None` is the
    permissive default for both, so it reads as the non-restrictive side, and a
    move that leaves the effective answer unchanged is not a change at all.
    """
    was, now = before is restrictive, after is restrictive
    if was == now:
        return None
    return not now


def _multiple_of_loosened(before: object, after: object) -> bool | None:
    """`multipleOf` as an exact ratio, so no float touches the comparison.

    A multiple of the old value accepts only its own points of the old set, so
    `2 -> 4` tightens; a divisor, `4 -> 2`, loosens. Values that are neither a
    multiple nor a divisor, like `2` and `3`, accept incommensurate sets and
    cannot be ordered here, so they are `other`.
    """
    try:
        was, now = Fraction(str(before)), Fraction(str(after))
    except (TypeError, ValueError):
        return None
    if was <= 0 or now <= 0:
        return None
    if (now / was).denominator == 1:
        return False
    if (was / now).denominator == 1:
        return True
    return None


#: A number's `format` ordered by the width it names. Every `float` is exactly a
#: `double` and not conversely, so the move to `double` accepts strictly more.
_NUMBER_FORMAT_WIDTH: Final[dict[str, int]] = {'float': 0, 'double': 1}


def _format_width(name: str) -> tuple[str, int] | None:
    """A `format` as its width, tagged by the table it comes from.

    Integer widths are ordered by `INTEGER_FORMATS` (an `int8` holds fewer values
    than an `int64`); number widths by `_NUMBER_FORMAT_WIDTH`. A `datetime`'s
    formats are graded before the bounds are reached, so this only ever sees the
    numeric ones; anything else answers `None` and grades as `other`.
    """
    if name in INTEGER_FORMATS:
        return 'integer', INTEGER_FORMATS[name]
    if name in _NUMBER_FORMAT_WIDTH:
        return 'number', _NUMBER_FORMAT_WIDTH[name]
    return None


def _format_loosened(before: object, after: object) -> bool | None:
    """`format` on an integer or number, by the width it names.

    A move to a wider width loosens (it accepts a superset) and one to a narrower
    tightens. A `datetime`'s formats are handled earlier, and widths from different
    tables would be a *type* change, not a format change, so both are `other`.
    """
    was = _format_width(str(before))
    now = _format_width(str(after))
    if was is None or now is None or was[0] != now[0]:
        return None
    if now[1] == was[1]:
        return None
    return now[1] > was[1]


def _datetime_format_changed(before: object, after: object) -> bool:
    """Whether a `datetime`'s wire representation changed, defaulting to RFC 3339.

    Its two formats are different spellings of the same instant, not a set one
    contains, so a move between them breaks a caller reading either; writing the
    default explicitly (or dropping it) is not a move, so it is not a change.
    """
    default = 'rfc3339'
    was = before if before in DATETIME_FORMATS else default
    now = after if after in DATETIME_FORMATS else default
    return was != now


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


# -- reporting -----------------------------------------------------------------


def worst(rules: Iterable[Rule]) -> Severity | None:
    """The most severe grading in `rules`, or `None` for none at all.

    What a CI gate reads. `diff` exits non-zero on `breaking`, and this is the
    one place that decides which of a list of gradings that is.
    """
    return _ORDER.worst(rule.severity for rule in rules)


def at_least(severity: Severity) -> frozenset[Severity]:
    """`severity` and everything worse — what `--severity` selects.

    A **threshold**, matching `lint`: one flag name cannot mean a threshold on
    one verb and an exact-set filter on another (docs/13 § 8).
    """
    return _ORDER.at_least(severity)


def record(rule: Rule, change: Change) -> dict[str, object]:
    """One graded change as JSON — the `--json` contract of `fastraml diff`.

    Here rather than in `cli.py` because it *is* the contract: a consumer that
    disagrees with the grading works from these facts (§ 10), so the shape is
    the view's to promise and not the presentation layer's to invent. `lint`'s
    `Finding.to_dict` sits beside its own view for the same reason.

    `directions` is a list, not one side. A type that is a POST body and a GET
    response is graded on the worse of the two, so a record naming only one of
    them contradicts its own `rule` — `direction: request` beside
    `response-property-optional` — and a consumer regrading these facts its own
    way cannot reach the same answer from them.

    `required` is the node's own `required` facet, present only for an added or
    removed property or parameter (a node is a property, not a delta), and `null`
    everywhere else — never to be confused with `required` as the *value* of
    `attribute` on a `changed` node.
    """
    return {
        'kind': change.kind,
        'iri': change.iri,
        'node_kind': change.node_kind,
        'directions': sorted(change.directions),
        'attribute': change.attribute,
        'before': plain(change.before),
        'after': plain(change.after),
        'required': change.required,
        'rule': rule.name,
        'severity': rule.severity,
        'because': rule.because,
    }


def plain(value: object) -> object:
    """A change's before/after as something `json.dumps` accepts.

    A multi-valued facet is a tuple in the model (`enum`, OAuth scopes) and a
    list on the wire; everything else already is what it looks like.
    """
    return list(value) if isinstance(value, tuple) else value
