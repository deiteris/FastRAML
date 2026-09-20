"""The vocabulary, the coordinates and the results — docs/16-graph.md section 10.

What a comparison can say, with nothing that says it. A coordinate names where a
change is, a result pairs that with what changed there, and `impact_of` grades a
rule by the one table both graders share. `compare` fills these in, `markdown`
and `records` read them, and none of the three needs the others to understand one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, Protocol, get_args

from fastraml.views.backward.rules import RULES
from fastraml.views.severity import Ranking

__all__ = [
    'IMPACTS',
    'RULE_IDS',
    'SUBJECTS',
    'BackwardChange',
    'Change',
    'ChangeKind',
    'Changed',
    'Direction',
    'Impact',
    'ItemsSegment',
    'Location',
    'OperationAdded',
    'OperationContract',
    'OperationId',
    'OperationRemoved',
    'ParameterLocation',
    'PathSegment',
    'PatternPropertySegment',
    'PropertySegment',
    'RequestBody',
    'ResponseBody',
    'ResponseStatus',
    'SchemaLocation',
    'SecurityLocation',
    'Subject',
    'TransportLocation',
    'UnionMemberSegment',
    'impact_of',
    'side_of',
]

type Impact = Literal['breaking', 'review', 'compatible', 'cosmetic']
type Direction = Literal['request', 'response']
type ChangeKind = Literal['added', 'removed', 'changed']

#: What a change is *about*, as a closed vocabulary. `attribute` names the field
#: within it and may be absent; `subject` never is.
#:
#: The two were one axis until a report read `additionalProperties: true -> false`
#: as "Required -> Optional". Nothing was wrong with the record: `before` and
#: `after` are `object`, so the renderer sniffed the Python type, found a `bool`,
#: and guessed the only meaning a bare boolean used to have. A closed subject is
#: what lets a reader -- and `_value` below -- ask what a value *is* instead of
#: inferring it from how Python happens to store it.
type Subject = Literal[
    'base-uri',
    'body',
    'constraint',
    'custom-facet',
    'documentation',
    'enum-value',
    'operation',
    'parameter',
    'pattern-property',
    'property',
    'protocol',
    'required',
    'response',
    'security',
    'security-alternative',
    'security-setting',
    'type',
    'union-member',
]

#: `Subject` at runtime, for validating what a project's `match.subject` names.
#: `get_args` over the alias rather than a second hand-kept list, which would be
#: one edit away from disagreeing with the type it is supposed to mirror.
SUBJECTS: Final = frozenset(get_args(Subject.__value__))


#: Every rule the comparison can emit -- the table's keys. Derived rather than
#: hand-kept, so an override the CLI accepts always names a rule something can
#: produce, which is the one failure an override must not have.
RULE_IDS: Final = frozenset(RULES)

#: Worst first, and the only ordering this view has. `severity.Ranking` is the
#: arithmetic every grading view shares; a private tuple-plus-dict copy of it
#: here is exactly what that module was written to stop.
IMPACTS: Final = Ranking[Impact](('breaking', 'review', 'compatible', 'cosmetic'))


def impact_of(rule: str) -> Impact:
    """What `rule` does to a caller, from the one table that grades rules."""
    return RULES[rule].impact


#: Movements whose rule id needs a side before the table can grade it. Removing
#: a property is `review` on the way in and `breaking` on the way out, so the
#: walk names *what moved* and the side comes from the coordinate.
#:
#: `property-added-required` has no response form on purpose: a new required
#: field in a response breaks nobody, which `response-property-added` already
#: says. It falls back to the unqualified movement rather than naming a rule the
#: table does not have.
_SIDED: Final = frozenset(
    {
        'constraint-loosened',
        'constraint-tightened',
        'enum-value-added',
        'enum-value-removed',
        'property-added',
        'property-added-required',
        'property-optional',
        'property-removed',
        'property-required',
    }
)


def rule_for(movement: str, direction: Direction | None) -> str:
    """The rule id for `movement` seen from `direction`.

    The walk used to build these itself, as `f'{direction}-property-removed'` at
    each site, which is why a change could only ever carry one grade: the side
    was baked in before the table was consulted. Naming the movement instead
    leaves the side a question this answers, and a coordinate with no side --
    a type declaration, which is neither sent nor received until someone uses
    it -- can be asked twice.
    """
    if movement not in _SIDED:
        return movement
    if direction is None:
        raise AssertionError(f'{movement} needs a side of the wire, and this coordinate has none')
    sided = f'{direction}-{movement}'
    return sided if sided in RULES else f'{direction}-{movement.rsplit("-", 1)[0]}'


#: An operation that arrived or left carries its grade as a field default, which
#: has to be a value rather than a call -- so the two the walk never computes are
#: read from the table here instead of written out beside it.
_OPERATION_ADDED: Final[Impact] = impact_of('entity-added')
_OPERATION_REMOVED: Final[Impact] = impact_of('entity-removed')


@dataclass(frozen=True, slots=True)
class OperationId:
    path: str
    method: str


@dataclass(frozen=True, slots=True)
class OperationContract:
    pass


@dataclass(frozen=True, slots=True)
class RequestBody:
    media_type: str


@dataclass(frozen=True, slots=True)
class ResponseBody:
    status: str
    media_type: str


@dataclass(frozen=True, slots=True)
class ResponseStatus:
    status: str


@dataclass(frozen=True, slots=True)
class ParameterLocation:
    binding: Literal['path', 'query', 'header', 'baseUri']
    name: str
    response_status: str | None = None


@dataclass(frozen=True, slots=True)
class SecurityLocation:
    pass


@dataclass(frozen=True, slots=True)
class TransportLocation:
    pass


#: Every coordinate a change can sit at. One union and not one per owner: an
#: API-level `protocols:` and an operation's own sit at the same
#: `TransportLocation`, and who owns them is `Changed.operation`, not a second
#: spelling of where they are. `OperationContract` is the one a root change
#: cannot use, because the API node has no method contract -- which `emit`
#: refuses rather than the type system, since it is a fact about the walk.
#:
#: `baseUri` is `TransportLocation` and not a root facet of its own: with
#: `protocols` it is how a caller reaches the API, and the two read as one fact.
type Location = (
    OperationContract
    | RequestBody
    | ResponseBody
    | ResponseStatus
    | ParameterLocation
    | SecurityLocation
    | TransportLocation
)

#: The coordinates a shape hangs off. Narrower than `Location` because a shape
#: has no contract, status or transport to sit at.
type SchemaLocation = RequestBody | ResponseBody | ParameterLocation


@dataclass(frozen=True, slots=True)
class PropertySegment:
    name: str


@dataclass(frozen=True, slots=True)
class ItemsSegment:
    pass


@dataclass(frozen=True, slots=True)
class PatternPropertySegment:
    pattern: str


@dataclass(frozen=True, slots=True)
class UnionMemberSegment:
    name: str | None
    type: str
    occurrence: int = 1


type PathSegment = PropertySegment | ItemsSegment | PatternPropertySegment | UnionMemberSegment


#: On an `added` or `removed` change, `before` and `after` carry a descriptor of
#: the entity -- but only what its coordinate does not already state. A property
#: and a parameter carry their type and requiredness, because `location` and
#: `path` hold only a name. A response, a body and a union member carry nothing:
#: the status is the `ResponseStatus`, the media type is the `RequestBody`, and
#: the member's type is already in its `UnionMemberSegment`. Restating them put
#: the same string in a row twice and gave a reader two places to check for one
#: fact. A `security-alternative` does carry its scheme name, because
#: `SecurityLocation` is empty and nothing else names it.
class BackwardChange(Protocol):
    impact: Impact
    rule: str


@dataclass(frozen=True, slots=True)
class Changed:
    """One change, at one coordinate.

    The coordinate is three fields and two of them are optional, which is the
    whole of it:

    ==================  ===========================================
    `operation is None`  an API-level default, compared once at the
                         root -- `protocols:`, `securedBy:`, a
                         `baseUriParameters` shape
    `path is None`       a contract change, not a shape one: the
                         coordinate in `location` *is* what moved
    `path == ()`         a shape change at the root of that shape
    ==================  ===========================================

    Those were four classes, one per cell of owner x path. Every rule that holds
    on both sides of either axis then had to be written twice -- requiredness
    and documentation each had two implementations differing only in which
    emitter they called -- and eleven `isinstance` checks downstream existed to
    work out which cell a change came from. Two optional fields say it once, and
    say it in the form a reader asks the question in.

    `direction` is deliberately absent: it is `side_of(location)`, and storing a
    function of a neighbouring field is how the two come to disagree.
    """

    operation: OperationId | None
    location: Location
    path: tuple[PathSegment, ...] | None
    kind: ChangeKind
    subject: Subject
    attribute: str | None
    before: object
    after: object
    impact: Impact
    rule: str


@dataclass(frozen=True, slots=True)
class OperationAdded:
    operation: OperationId
    display_name: str | None = None
    description: str | None = None
    impact: Impact = _OPERATION_ADDED
    rule: str = 'entity-added'


@dataclass(frozen=True, slots=True)
class OperationRemoved:
    operation: OperationId
    display_name: str | None = None
    description: str | None = None
    impact: Impact = _OPERATION_REMOVED
    rule: str = 'entity-removed'


#: An operation that arrived or left is not a change *at* a coordinate, it is
#: the coordinate -- which is why those two stay their own results rather than
#: becoming a third pair of optional fields on `Changed`.
type Change = Changed | OperationAdded | OperationRemoved


def side_of(location: Location) -> Direction | None:
    """Which side of the wire a coordinate sits on, or `None` for neither.

    The walk already decides this when it grades -- a header under a response
    status is compared as `response`, the same header under a request as
    `request` -- but it decided it from the *traversal* and then threw it away.
    Recovering it from the coordinate makes it a property of the change rather
    than of the order the walk happened to visit things in, which is what lets a
    reader be shown "what I send" apart from "what I receive".

    Transport and security are the caller's side: a protocol it may no longer
    speak and a credential it must now present both stop the request before a
    response exists. `OperationContract` and `ApiContract` are neither -- a
    `description` changed on no side of the wire at all.
    """
    if isinstance(location, OperationContract):
        return None
    if isinstance(location, (ResponseBody, ResponseStatus)):
        return 'response'
    if isinstance(location, ParameterLocation):
        return 'response' if location.response_status is not None else 'request'
    return 'request'


def _path_label(path: tuple[PathSegment, ...]) -> str:
    """A path into a shape, written out: `$.customer.email`, `$.items[].sku`.

    In the model and not in `markdown` because it is not only a rendering. The
    report writes a path into a cell with it, and `records._matches` writes one
    to compare against a project's `match.path`, so this spelling *is* the
    config contract -- an edit here changes which overrides fire, not only how a
    table reads. It is the one thing both readers of the model spell the same
    way, and the reason it is here rather than in either of them.
    """
    out = '$'
    for segment in path:
        if isinstance(segment, PropertySegment):
            out += (
                f'.{segment.name}'
                if segment.name.isidentifier()
                else f'[{json.dumps(segment.name, ensure_ascii=False)}]'
            )
        elif isinstance(segment, ItemsSegment):
            out += '[]'
        elif isinstance(segment, PatternPropertySegment):
            out += f'[/{segment.pattern}/]'
        else:
            member = segment.name or segment.type
            occurrence = f'#{segment.occurrence}' if segment.occurrence > 1 else ''
            out += f'<{member}{occurrence}>'
    return out


def _normal(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _normal(child)) for key, child in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normal(child) for child in value)
    return value
