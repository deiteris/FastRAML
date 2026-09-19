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
    'ApiChanged',
    'ApiSchemaChanged',
    'BackwardChange',
    'Change',
    'ChangeKind',
    'Direction',
    'Impact',
    'ItemsSegment',
    'LocatedChange',
    'OperationAdded',
    'OperationChanged',
    'OperationContract',
    'OperationId',
    'OperationLocation',
    'OperationRemoved',
    'ParameterLocation',
    'PathSegment',
    'PatternPropertySegment',
    'PropertySegment',
    'RequestBody',
    'ResponseBody',
    'ResponseStatus',
    'SchemaChanged',
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


type OperationLocation = (
    OperationContract
    | RequestBody
    | ResponseBody
    | ResponseStatus
    | ParameterLocation
    | SecurityLocation
    | TransportLocation
)

#: Where an API-scoped change sits: the coordinates an operation uses, minus the
#: operation's own contract, which the root does not have. An API-level
#: `protocols:` or `securedBy:` is compared once at the root, and a security
#: scheme's `describedBy` puts request headers and responses under that one
#: comparison. `ApiChanged` is `OperationChanged` without an owner, exactly as
#: `ApiSchemaChanged` is `SchemaChanged` without one.
#:
#: `baseUri` is `TransportLocation` and not a root facet of its own: with
#: `protocols` it is how a caller reaches the API, and the two read as one fact.
type ApiLocation = (
    RequestBody | ResponseBody | ResponseStatus | ParameterLocation | SecurityLocation | TransportLocation
)
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
class ApiChanged:
    location: ApiLocation
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


@dataclass(frozen=True, slots=True)
class ApiSchemaChanged:
    location: ParameterLocation
    path: tuple[PathSegment, ...]
    kind: ChangeKind
    subject: Subject
    attribute: str | None
    before: object
    after: object
    impact: Impact
    rule: str


@dataclass(frozen=True, slots=True)
class OperationChanged:
    operation: OperationId
    location: OperationLocation
    kind: ChangeKind
    subject: Subject
    attribute: str | None
    before: object
    after: object
    impact: Impact
    rule: str


@dataclass(frozen=True, slots=True)
class SchemaChanged:
    operation: OperationId
    location: SchemaLocation
    path: tuple[PathSegment, ...]
    kind: ChangeKind
    subject: Subject
    attribute: str | None
    before: object
    after: object
    impact: Impact
    rule: str


type Change = ApiChanged | ApiSchemaChanged | OperationAdded | OperationRemoved | OperationChanged | SchemaChanged

#: Every result that names a coordinate: the four that carry `location`, `kind`,
#: `subject`, `attribute` and a value pair. They differ only in who owns them and
#: whether a `path` reaches inside a shape, so one renderer reads all four.
#: `OperationAdded` and `OperationRemoved` are outside it -- an operation that
#: arrived or left is not a change *at* a coordinate, it is the coordinate.
type LocatedChange = ApiChanged | ApiSchemaChanged | OperationChanged | SchemaChanged


def side_of(location: ApiLocation | OperationLocation) -> Direction | None:
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
