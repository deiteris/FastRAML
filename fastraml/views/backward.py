"""Operation-local backward compatibility over two effective RAML models."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from typing import TYPE_CHECKING, Final, Literal, Protocol, get_args

from fastraml.parser.fragments import APIFragment
from fastraml.types.base import BaseShape, Parameter, facets_of
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import DATETIME_FORMATS, INTEGER_FORMATS, DateTimeShape, FileShape

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from fastraml.config import CompatibilityConfig, CompatibilityMatch
    from fastraml.parser.directives import SecurityScheme
    from fastraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
    from fastraml.parser.security import SecuritySchemeDescription
    from fastraml.registry import Raml

__all__ = [
    'ApiChanged',
    'ApiSchemaChanged',
    'BackwardChange',
    'ItemsSegment',
    'OperationAdded',
    'OperationChanged',
    'OperationId',
    'OperationRemoved',
    'PatternPropertySegment',
    'PropertySegment',
    'SchemaChanged',
    'UnionMemberSegment',
    'backward',
    'backward_markdown',
    'configure',
    'record',
    'render_markdown',
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


#: On an `added` or `removed` change, `before` and `after` carry a descriptor of
#: the entity -- but only what its coordinate does not already state. A property
#: and a parameter carry their type and requiredness, because `location` and
#: `path` hold only a name. A response, a body and a union member carry nothing:
#: the status is the `ResponseStatus`, the media type is the `RequestBody`, and
#: the member's type is already in its `UnionMemberSegment`. Restating them put
#: the same string in a row twice and gave a reader two places to check for one
#: fact. A `security-alternative` does carry its scheme name, because
#: `SecurityLocation` is empty and nothing else names it.


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
    impact: Impact = 'compatible'
    rule: str = 'entity-added'


@dataclass(frozen=True, slots=True)
class OperationRemoved:
    operation: OperationId
    display_name: str | None = None
    description: str | None = None
    impact: Impact = 'breaking'
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
type AnySchemaChanged = ApiSchemaChanged | SchemaChanged

RULE_IDS: Final = frozenset(
    {
        'base-uri-changed',
        'documentation-changed',
        'entity-added',
        'entity-removed',
        'format-changed',
        'other',
        'protocol-added',
        'protocol-removed',
        'reference-retargeted',
        'request-constraint-loosened',
        'request-constraint-tightened',
        'request-enum-value-added',
        'request-enum-value-removed',
        'request-property-added',
        'request-property-added-required',
        'request-property-optional',
        'request-property-removed',
        'request-property-required',
        'response-constraint-loosened',
        'response-constraint-tightened',
        'response-enum-value-added',
        'response-enum-value-removed',
        'response-property-added',
        'response-property-optional',
        'response-property-removed',
        'response-property-required',
        'security-added',
        'security-alternative-added',
        'security-alternative-removed',
        'security-removed',
        'type-changed',
    }
)


def backward(old: Raml, new: Raml) -> list[Change]:
    """Compare the callable operations of two models parsed with ``unwrap=True``."""
    analyzer = _Backward(old, new)
    analyzer.run()
    return analyzer.changes


def backward_markdown(old: Raml, new: Raml) -> str:
    return render_markdown(backward(old, new))


def configure(changes: Sequence[Change], config: CompatibilityConfig) -> list[Change]:
    """Apply ordered project overrides to compatibility results."""
    unknown = sorted({setting.id for setting in config.rules} - RULE_IDS)
    if unknown:
        raise ValueError(f'unknown compatibility rule: {unknown[0]}')
    # Checked for the same reason the rule id is: a `subject:` nobody emits matches
    # nothing, so a typo reads as a policy that silently never fires -- the one
    # failure an override must not have, since it is written to change a verdict.
    unknown_subjects = sorted(
        {setting.match.subject for setting in config.rules if setting.match and setting.match.subject} - SUBJECTS
    )
    if unknown_subjects:
        raise ValueError(f'unknown compatibility subject: {unknown_subjects[0]}')
    configured: list[Change] = []
    for original in changes:
        change: Change | None = original
        for setting in config.rules:
            if change is None or setting.id != change.rule or not _matches(change, setting.match):
                continue
            if setting.disabled:
                change = None
            elif setting.impact is not None:
                change = replace(change, impact=setting.impact)
        if change is not None:
            configured.append(change)
    return configured


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


def render_markdown(changes: Sequence[Change]) -> str:
    """The reading view: grouped the way a caller reads, not the way the walk built it.

    Each operation is split into what it sends and what it receives, because those
    are a consumer's two questions and the answer to each is actionable on its
    own. Grouping by result type instead put a parameter's requiredness and its
    type in different tables under different headings, so "what happened to
    `limit`?" took two lookups and an understanding of which Python class
    produced which row.

    Rows within a table are sorted by impact, stably, so a break never sits below
    a documentation edit and same-impact rows keep the order the document declared
    them in. The sort lives here and not in `backward` because `impact` is the one
    field `configure` rewrites -- ordering the change list by a grading a project
    is free to override would leave the list stale the moment one did.
    """
    api = [change for change in changes if isinstance(change, (ApiChanged, ApiSchemaChanged))]
    added = [change for change in changes if isinstance(change, OperationAdded)]
    removed = [change for change in changes if isinstance(change, OperationRemoved)]
    shared, owned_by = _rollup(changes)
    entries: list[Change] = [*api, *added, *removed, *(entry.change for entry in shared)]
    entries.extend(change for owned in owned_by.values() for change in owned)
    counts = {impact: sum(entry.impact == impact for entry in entries) for impact in _IMPACTS}
    result = 'Breaking' if counts['breaking'] else ('Review required' if counts['review'] else 'Compatible')
    callout = 'CAUTION' if counts['breaking'] else ('WARNING' if counts['review'] else 'TIP')
    lines = [
        '# API compatibility',
        '',
        f'> [!{callout}]',
        f'> **{result}.** {_verdict_sentence(counts)}',
        '',
        '| Impact | Changes |',
        '|---|---:|',
        f'| Breaking | {counts["breaking"]} |',
        f'| Review required | {counts["review"]} |',
        f'| Compatible | {counts["compatible"]} |',
        f'| Documentation | {counts["cosmetic"]} |',
        *_LEGEND,
    ]
    if api:
        lines.extend(('', '## Every operation', '', 'Declared once at the API root, so these reach every operation.'))
        lines.extend(_sided_tables([_Entry(change) for change in api]))
    if removed or added:
        lines.extend(('', '## Operations added and removed'))
    if removed:
        lines.extend(('', '### Removed', ''))
        lines.extend(_operation_markdown(change) for change in removed)
    if added:
        lines.extend(('', '### Added', ''))
        lines.extend(_operation_markdown(change) for change in added)
    if shared:
        lines.extend(('', '## Several operations', '', 'One edit each, reaching the operations named on the row.'))
        lines.extend(_sided_tables(shared))
    for operation, owned in owned_by.items():
        lines.extend(('', f'## {_inline_code(f"{operation.method.upper()} {operation.path}")}'))
        lines.extend(_sided_tables([_Entry(change) for change in owned]))
    return '\n'.join(lines) + '\n'


@dataclass(frozen=True, slots=True)
class _Entry:
    """A row, and every operation it says the same thing about."""

    change: LocatedChange
    operations: tuple[OperationId, ...] = ()


def _rollup(changes: Sequence[Change]) -> tuple[list[_Entry], dict[OperationId, list[LocatedChange]]]:
    """One edit reaching many operations is one row, not one row per operation.

    The change *list* is right to hold them apart: a caller of `/orders` and a
    caller of `/invoices` hold different contracts, and `configure` matches each
    on its own operation. But a type shared by four of them, edited once, produced
    an identical row under every one -- four tables and a headline of "4 breaking
    changes" for a single `maxLength`, which counts the blast radius as though it
    were the number of decisions a reader has to make.

    Grouped on everything except the owner, so only rows stating the same fact
    collapse. A change reaching one operation stays under it.
    """
    groups: dict[object, list[OperationChanged | SchemaChanged]] = {}
    for change in changes:
        if isinstance(change, (OperationChanged, SchemaChanged)):
            groups.setdefault(_shared_key(change), []).append(change)
    shared: list[_Entry] = []
    owned_by: dict[OperationId, list[LocatedChange]] = {}
    for members in groups.values():
        if len(members) > 1:
            shared.append(_Entry(members[0], tuple(change.operation for change in members)))
        else:
            owned_by.setdefault(members[0].operation, []).append(members[0])
    return shared, owned_by


def _shared_key(change: OperationChanged | SchemaChanged) -> object:
    path = change.path if isinstance(change, SchemaChanged) else ()
    return (
        change.location,
        path,
        change.kind,
        change.subject,
        change.attribute,
        _normal(change.before),
        _normal(change.after),
        change.impact,
        change.rule,
    )


#: What a section heading means, stated once. These are definitions rather than
#: findings, so they belong where a reader meets them first and nowhere else --
#: under every table they would be eighty lines saying the same six things.
_LEGEND: Final[tuple[str, ...]] = (
    '',
    '## How to read this',
    '',
    'Each operation is split into what a caller **sends** and what it **receives**,',
    'then into what was removed, changed and added on that side. Worst first.',
    '',
    '| Section | Request | Response |',
    '|---|---|---|',
    '| **Removed** | the API no longer reads it | the API no longer returns it |',
    '| **Changed** | the old form may now be rejected | a value you did not expect may arrive |',
    '| **Added** | new, and breaking only if required | returned as well; ignore it and nothing breaks |',
    '',
    'A row names **Where** the change is, and a **Path** when it reaches inside a',
    'shape. The value column is headed by what it holds -- **Type**, **Value**,',
    '**Scheme** -- or **Detail** where a table mixes them, in which case a **What**',
    'column says which each row is. A change states `old -> new`. Columns a table',
    'has no use for are left out of it.',
)

#: Request before response, because a caller fixes what it sends before it can
#: see what it receives. `None` is the documentation bucket: prose that changed
#: on no side of the wire, kept out of both so neither reads as actionable.
_SIDES: Final[tuple[tuple[Direction | None, str], ...]] = (
    ('request', 'Request'),
    ('response', 'Response'),
    (None, 'Documentation'),
)


#: Worst first within a side, so "read the breaking things first" survives the
#: split into kinds. `Removed` before `Changed` before `Added` only breaks ties.
_KINDS: Final[tuple[ChangeKind, ...]] = ('removed', 'changed', 'added')


def _sided_tables(entries: Sequence[_Entry]) -> list[str]:
    """One table per side and kind, and no column that every row leaves blank.

    Split by kind because `Before` and `After` fitted only one of the three. An
    addition has no before and a removal has no after, so half the worked
    catalogue's rows spent a two-column pair to carry one value -- and eight of
    them carried none, their coordinate having said it all. A `Changed` table
    states its transition in one `Detail` cell; an `Added` or `Removed` one needs
    no `Change` column at all, because its heading is the verb.

    What each kind means for a caller is a definition, not a fact about this
    comparison, so it is stated once in the legend rather than under forty
    tables.
    """
    lines: list[str] = []
    for side, heading in _SIDES:
        rows = [entry for entry in entries if side_of(entry.change.location) == side]
        if not rows:
            continue
        lines.extend(('', f'### {heading}'))
        groups = [(kind, [row for row in rows if row.change.kind == kind]) for kind in _KINDS]
        for kind, group in sorted(
            (pair for pair in groups if pair[1]), key=lambda pair: min(_RANK[row.change.impact] for row in pair[1])
        ):
            lines.extend(('', f'**{kind.title()}**', ''))
            lines.extend(_kind_table(group, side_stated=side is not None, transition=kind == 'changed'))
    return lines


#: What the value cell holds, per subject. It is not one thing: a property's is a
#: type, an enum member's is a value and a scheme's is a name, so heading all
#: three `Detail` made the reader work out which. It matters most for an enum,
#: whose `Where` and `Path` address the *property* rather than the thing that
#: moved -- the one row in the report where the coordinate is not the subject.
_VALUE_NOUN: Final[dict[Subject, str]] = {
    'parameter': 'Type',
    'pattern-property': 'Type',
    'property': 'Type',
    'enum-value': 'Value',
    'security-alternative': 'Scheme',
}


def _kind_table(rows: Sequence[_Entry], *, side_stated: bool, transition: bool) -> list[str]:
    paths = any(isinstance(row.change, (ApiSchemaChanged, SchemaChanged)) and row.change.path for row in rows)
    details = any(_detail(row.change) for row in rows)
    described = any(_description(row.change) for row in rows)
    operations = any(row.operations for row in rows)
    # A `changed` row holds a transition whatever its subject, and its `Change`
    # column has already named the facet. Only the other two kinds get the noun,
    # and only where every row that fills the cell agrees on one; a mixed table
    # falls back to `Detail` and earns a `What` column to say which is which.
    nouns = {_VALUE_NOUN.get(row.change.subject, 'Detail') for row in rows if _detail(row.change)}
    subjects = not transition and len(nouns) > 1
    value = 'Detail' if transition or len(nouns) != 1 else nouns.pop()
    columns = ['Where', *(['Path'] if paths else []), *(['Change'] if transition else [])]
    columns.extend([*(['What'] if subjects else []), *([value] if details else [])])
    columns.extend([*(['Description'] if described else [])])
    columns.extend(['Compatibility', *(['Operations'] if operations else [])])
    return [
        f'| {" | ".join(columns)} |',
        f'|{"---|" * len(columns)}',
        *(
            _change_row(
                entry,
                side_stated=side_stated,
                paths=paths,
                change=transition,
                subjects=subjects,
                details=details,
                described=described,
                operations=operations,
            )
            for entry in _by_impact(rows)
        ),
    ]


def _by_impact(entries: Sequence[_Entry]) -> list[_Entry]:
    """Most costly first, ties in declaration order -- `sorted` is stable."""
    return sorted(entries, key=lambda entry: _RANK[entry.change.impact])


def record(change: Change) -> dict[str, object]:
    if isinstance(change, ApiChanged):
        return {
            'scope': 'api',
            'kind': change.kind,
            'subject': change.subject,
            'attribute': change.attribute,
            'before': change.before,
            'after': change.after,
            'impact': change.impact,
            'rule': change.rule,
        }
    if isinstance(change, ApiSchemaChanged):
        return {
            'scope': 'api-schema',
            'kind': change.kind,
            'location': _location_record(change.location),
            'path': [_segment_record(segment) for segment in change.path],
            'subject': change.subject,
            'attribute': change.attribute,
            'before': change.before,
            'after': change.after,
            'impact': change.impact,
            'rule': change.rule,
        }
    base: dict[str, object] = {
        'operation': {'path': change.operation.path, 'method': change.operation.method},
        'impact': change.impact,
        'rule': change.rule,
    }
    if isinstance(change, OperationAdded):
        return base | {
            'kind': 'operation-added',
            'display_name': change.display_name,
            'description': change.description,
        }
    if isinstance(change, OperationRemoved):
        return base | {
            'kind': 'operation-removed',
            'display_name': change.display_name,
            'description': change.description,
        }
    detail = {
        'kind': change.kind,
        'location': _location_record(change.location),
        'subject': change.subject,
        'attribute': change.attribute,
        'before': change.before,
        'after': change.after,
    }
    if isinstance(change, SchemaChanged):
        return base | {'scope': 'schema', 'path': [_segment_record(segment) for segment in change.path]} | detail
    return base | {'scope': 'operation'} | detail


_IMPACTS: Final[tuple[Impact, ...]] = ('breaking', 'review', 'compatible', 'cosmetic')
_RANK: Final = {impact: index for index, impact in enumerate(_IMPACTS)}
_NUMBER_WIDTH: Final = {'float': 0, 'double': 1}


class _Backward:
    __slots__ = ('changes', 'new', 'new_api', 'old', 'old_api')

    def __init__(self, old: Raml, new: Raml) -> None:
        self.old = old
        self.new = new
        self.old_api = old.entry_point if isinstance(old.entry_point, APIFragment) else None
        self.new_api = new.entry_point if isinstance(new.entry_point, APIFragment) else None
        self.changes: list[Change] = []

    def run(self) -> None:
        self.base_uri()
        self.api_parameters()
        self.api_protocols()
        self.api_security()
        old_operations = _operations(self.old)
        new_operations = _operations(self.new)
        for operation_id, old_pair in old_operations.items():
            new_pair = new_operations.get(operation_id)
            if new_pair is None:
                operation = old_pair[1]
                self.changes.append(
                    OperationRemoved(
                        operation_id, _text_facet(operation.display_name), _text_facet(operation.description)
                    )
                )
                continue
            self.operation(operation_id, old_pair, new_pair)
        for operation_id in new_operations:
            if operation_id in old_operations:
                continue
            operation = new_operations[operation_id][1]
            self.changes.append(
                OperationAdded(operation_id, _text_facet(operation.display_name), _text_facet(operation.description))
            )

    def operation(
        self,
        operation_id: OperationId,
        old_pair: tuple[EndPoint, Operation],
        new_pair: tuple[EndPoint, Operation],
    ) -> None:
        old_endpoint, old = old_pair
        new_endpoint, new = new_pair
        self.protocols(operation_id, old, new)
        self.parameters(operation_id, old_endpoint.uri_parameters, new_endpoint.uri_parameters, 'path')
        if old.explicit_secured_by or new.explicit_secured_by:
            self.security(operation_id, old.secured_by, new.secured_by)
        self.request(operation_id, old.request, new.request)
        self.responses(operation_id, old.responses, new.responses)
        self.operation_scalar(
            operation_id,
            OperationContract(),
            'documentation',
            'displayName',
            _facet(old.display_name),
            _facet(new.display_name),
            'documentation-changed',
            'cosmetic',
        )
        self.operation_scalar(
            operation_id,
            OperationContract(),
            'documentation',
            'description',
            _facet(old.description),
            _facet(new.description),
            'documentation-changed',
            'cosmetic',
        )

    def base_uri(self) -> None:
        old = _facet(self.old_api.base_uri) if self.old_api is not None else None
        new = _facet(self.new_api.base_uri) if self.new_api is not None else None
        if old != new:
            self.changes.append(
                ApiChanged(
                    TransportLocation(), 'changed', 'base-uri', 'baseUri', old, new, 'breaking', 'base-uri-changed'
                )
            )

    def api_parameters(self) -> None:
        old = {} if self.old_api is None else self.old_api.base_uri_parameters
        new = {} if self.new_api is None else self.new_api.base_uri_parameters
        for name, before in old.items():
            after = new.get(name)
            location = ParameterLocation('baseUri', name)
            if after is None:
                self.api_schema_change(
                    location,
                    (),
                    'removed',
                    'parameter',
                    None,
                    _parameter(before),
                    None,
                    'request-property-removed',
                    'review',
                )
                continue
            if before.required != after.required:
                became = 'required' if after.required else 'optional'
                self.api_schema_change(
                    location,
                    (),
                    'changed',
                    'required',
                    'required',
                    before.required,
                    after.required,
                    f'request-property-{became}',
                    'breaking' if after.required else 'compatible',
                )
            self.shape(None, location, (), before.base, after.base, 'request', {})
        for name, after in new.items():
            if name in old:
                continue
            self.api_schema_change(
                ParameterLocation('baseUri', name),
                (),
                'added',
                'parameter',
                None,
                None,
                _parameter(after),
                'request-property-added-required' if after.required else 'request-property-added',
                'breaking' if after.required else 'compatible',
            )

    def api_protocols(self) -> None:
        old, new = _api_protocols(self.old_api), _api_protocols(self.new_api)
        if old == new:
            return
        rule, impact = _protocol_rule(old, new)
        self.operation_change(None, TransportLocation(), 'changed', 'protocol', 'protocols', old, new, rule, impact)

    def api_security(self) -> None:
        """The root's `securedBy:`, compared once for every method that inherits it.

        The same rule `api_protocols` follows, and for the same reason: an edit to
        an API-level default is one change at its source. A method that states its
        own `securedBy:` is compared in `operation` instead, which is what
        `explicit_secured_by` records.

        The whole comparison moves up, not only the alternative list -- a scheme's
        settings and its `describedBy` headers, query parameters and responses are
        equally the root's when the root is what named the scheme. That is why the
        walkers below take `OperationId | None` rather than this method
        reimplementing them against a different result type.
        """
        self.security(None, self.old.global_secured_by, self.new.global_secured_by)

    def protocols(self, operation: OperationId, old_operation: Operation, new_operation: Operation) -> None:
        """Only where the operation states its own -- `api_protocols` said the rest.

        `protocols:` is an API-level default a method may override, so an edit at
        the root is one change that reaches every method, not one change per
        method. Reported per method it was 33 of this catalogue's 34 operations
        carrying an identical row, and 33 of its 54 breaking changes: a reader
        counting the damage would have put it at two and a half times its size.

        This is [docs/16] section 10.1's argument for `baseUri`, which has no
        method-level override at all. `protocols:` has one, so the rule is
        conditional rather than absolute -- an operation that declares its own is
        making its own statement and is compared here.
        """
        old_declared, new_declared = tuple(old_operation.protocols), tuple(new_operation.protocols)
        if not old_declared and not new_declared:
            return
        old = old_declared or _api_protocols(self.old_api)
        new = new_declared or _api_protocols(self.new_api)
        if old == new:
            return
        rule, impact = _protocol_rule(old, new)
        self.operation_change(
            operation, TransportLocation(), 'changed', 'protocol', 'protocols', old, new, rule, impact
        )

    def request(self, operation: OperationId, old: Request | None, new: Request | None) -> None:
        self.parameters(operation, {} if old is None else old.headers, {} if new is None else new.headers, 'header')
        self.parameters(
            operation,
            {} if old is None else old.query_parameters,
            {} if new is None else new.query_parameters,
            'query',
        )
        self.bodies(operation, {} if old is None else old.bodies, {} if new is None else new.bodies, 'request')
        self.optional_shape(
            operation,
            ParameterLocation('query', 'queryString'),
            None if old is None else old.query_string,
            None if new is None else new.query_string,
            'request',
            (),
        )

    def responses(
        self,
        operation: OperationId | None,
        old: Mapping[str, Response],
        new: Mapping[str, Response],
    ) -> None:
        for status, before in old.items():
            after = new.get(status)
            if after is None:
                self.operation_change(
                    operation,
                    ResponseStatus(status),
                    'removed',
                    'response',
                    None,
                    _descriptor(before.description),
                    None,
                    'entity-removed',
                    'breaking',
                )
                continue
            self.parameters(operation, before.headers, after.headers, 'header', response_status=status)
            self.bodies(operation, before.bodies, after.bodies, 'response', status=status)
            self.operation_scalar(
                operation,
                ResponseStatus(status),
                'documentation',
                'displayName',
                _facet(before.display_name),
                _facet(after.display_name),
                'documentation-changed',
                'cosmetic',
            )
            self.operation_scalar(
                operation,
                ResponseStatus(status),
                'documentation',
                'description',
                _facet(before.description),
                _facet(after.description),
                'documentation-changed',
                'cosmetic',
            )
        for status in new:
            if status in old:
                continue
            self.operation_change(
                operation,
                ResponseStatus(status),
                'added',
                'response',
                None,
                None,
                _descriptor(new[status].description),
                'entity-added',
                'compatible',
            )

    def parameters(
        self,
        operation: OperationId | None,
        old: Mapping[str, Parameter],
        new: Mapping[str, Parameter],
        binding: Literal['path', 'query', 'header', 'baseUri'],
        *,
        response_status: str | None = None,
    ) -> None:
        direction: Direction = 'response' if response_status is not None else 'request'
        for name, before in old.items():
            after = new.get(name)
            location = ParameterLocation(binding, name, response_status)
            if after is None:
                rule = f'{direction}-property-removed'
                impact: Impact = 'breaking' if direction == 'response' else 'review'
                self.operation_change(
                    operation, location, 'removed', 'parameter', None, _parameter(before), None, rule, impact
                )
                continue
            self.parameter_required(operation, location, before.required, after.required, direction)
            self.shape(operation, location, (), before.base, after.base, direction, {})
        for name, after in new.items():
            if name in old:
                continue
            location = ParameterLocation(binding, name, response_status)
            if direction == 'request' and after.required:
                rule, impact = 'request-property-added-required', 'breaking'
            else:
                rule, impact = f'{direction}-property-added', 'compatible'
            self.operation_change(
                operation, location, 'added', 'parameter', None, None, _parameter(after), rule, impact
            )

    def bodies(
        self,
        operation: OperationId | None,
        old: Mapping[str, Body],
        new: Mapping[str, Body],
        direction: Direction,
        *,
        status: str | None = None,
    ) -> None:
        for media_type, before in old.items():
            after = new.get(media_type)
            location = RequestBody(media_type) if direction == 'request' else ResponseBody(status or '', media_type)
            if after is None:
                self.operation_change(
                    operation,
                    location,
                    'removed',
                    'body',
                    None,
                    _shape_described(before.shape),
                    None,
                    'entity-removed',
                    'breaking',
                )
                continue
            self.optional_shape(operation, location, before.shape, after.shape, direction, ())
        for media_type in new:
            if media_type in old:
                continue
            location = RequestBody(media_type) if direction == 'request' else ResponseBody(status or '', media_type)
            self.operation_change(
                operation,
                location,
                'added',
                'body',
                None,
                None,
                _shape_described(new[media_type].shape),
                'entity-added',
                'compatible',
            )

    def security(self, operation: OperationId | None, old: list[SecurityScheme], new: list[SecurityScheme]) -> None:
        location = SecurityLocation()
        old_open = not old or any(scheme.is_null for scheme in old)
        new_open = not new or any(scheme.is_null for scheme in new)
        if old_open != new_open:
            rule = 'security-added' if not new_open else 'security-removed'
            impact: Impact = 'breaking' if not new_open else 'compatible'
            self.operation_change(
                operation, location, 'changed', 'security', 'required', not old_open, not new_open, rule, impact
            )
        if old_open or new_open:
            return
        old_by_name = {scheme.name: scheme for scheme in old}
        new_by_name = {scheme.name: scheme for scheme in new}
        for name, before in old_by_name.items():
            after = new_by_name.get(name)
            if after is None:
                self.operation_change(
                    operation,
                    location,
                    'removed',
                    'security-alternative',
                    'name',
                    _scheme(before),
                    None,
                    'security-alternative-removed',
                    'breaking',
                )
                continue
            old_scopes = tuple(sorted(before.compiled_params or ()))
            new_scopes = tuple(sorted(after.compiled_params or ()))
            if old_scopes != new_scopes:
                added = set(new_scopes) - set(old_scopes)
                rule = 'security-added' if added else 'security-removed'
                impact = 'breaking' if added else 'compatible'
                self.operation_change(
                    operation, location, 'changed', 'security', 'scopes', old_scopes, new_scopes, rule, impact
                )
            old_settings = _scheme_definition(before)
            new_settings = _scheme_definition(after)
            for setting in sorted(old_settings.keys() | new_settings.keys()):
                old_value, new_value = old_settings.get(setting), new_settings.get(setting)
                if old_value != new_value:
                    self.operation_change(
                        operation,
                        location,
                        'changed',
                        'security-setting',
                        setting,
                        old_value,
                        new_value,
                        'reference-retargeted',
                        'review',
                    )
            old_description = None if before.definition is None else before.definition.described_by
            new_description = None if after.definition is None else after.definition.described_by
            self.security_description(operation, old_description, new_description)
        for name, arrival in new_by_name.items():
            if name in old_by_name:
                continue
            self.operation_change(
                operation,
                location,
                'added',
                'security-alternative',
                'name',
                None,
                _scheme(arrival),
                'security-alternative-added',
                'compatible',
            )

    def security_description(
        self,
        operation: OperationId | None,
        old: SecuritySchemeDescription | None,
        new: SecuritySchemeDescription | None,
    ) -> None:
        self.parameters(operation, {} if old is None else old.headers, {} if new is None else new.headers, 'header')
        self.parameters(
            operation,
            {} if old is None else old.query_parameters,
            {} if new is None else new.query_parameters,
            'query',
        )
        self.responses(operation, {} if old is None else old.responses, {} if new is None else new.responses)

    def parameter_required(
        self,
        operation: OperationId | None,
        location: ParameterLocation,
        old: bool,  # noqa: FBT001 - the old declaration value
        new: bool,  # noqa: FBT001 - the new declaration value
        direction: Direction,
    ) -> None:
        if old == new:
            return
        became = 'required' if new else 'optional'
        rule = f'{direction}-property-{became}'
        impact: Impact = 'breaking' if (direction == 'request') == new else 'compatible'
        self.operation_change(operation, location, 'changed', 'required', 'required', old, new, rule, impact)

    def operation_scalar(  # noqa: PLR0913, PLR0917 - a scalar delta plus its operation coordinate and policy
        self,
        operation: OperationId | None,
        location: OperationLocation,
        subject: Subject,
        attribute: str,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        if before != after:
            self.operation_change(operation, location, 'changed', subject, attribute, before, after, rule, impact)

    def operation_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        operation: OperationId | None,
        location: OperationLocation,
        kind: ChangeKind,
        subject: Subject,
        attribute: str | None,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        """No owner means an API-level default, compared once at the root.

        The mirror of `schema_change` below. `OperationContract` is the one
        coordinate that cannot arrive here without one -- the API node's own
        facets are `ApiContract` -- so it is the one case this refuses.
        """
        if operation is None:
            if isinstance(location, OperationContract):
                raise AssertionError('the API root has no operation contract to change')
            self.changes.append(ApiChanged(location, kind, subject, attribute, before, after, impact, rule))
        else:
            self.changes.append(
                OperationChanged(operation, location, kind, subject, attribute, before, after, impact, rule)
            )

    def optional_shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        old: BaseShape | None,
        new: BaseShape | None,
        direction: Direction,
        path: tuple[PathSegment, ...],
    ) -> None:
        if old is None or new is None:
            if old is not new:
                kind: ChangeKind = 'added' if old is None else 'removed'
                self.schema_change(
                    operation,
                    location,
                    path,
                    kind,
                    'type',
                    None,
                    None if old is None else old.type,
                    None if new is None else new.type,
                    'type-changed',
                    'breaking',
                )
            return
        self.shape(operation, location, path, old, new, direction, {})

    def shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: BaseShape,
        new: BaseShape,
        direction: Direction,
        ancestors: dict[BaseShape, BaseShape],
    ) -> None:
        old_kind, new_kind = old.shape, new.shape
        if old_kind is None or new_kind is None or type(old_kind) is not type(new_kind):
            self.schema_change(
                operation, location, path, 'changed', 'type', 'type', old.type, new.type, 'type-changed', 'breaking'
            )
            return
        if isinstance(old_kind, RecursiveShape) or isinstance(new_kind, RecursiveShape):
            if not (
                isinstance(old_kind, RecursiveShape)
                and isinstance(new_kind, RecursiveShape)
                and (old_kind.head.name, old_kind.head.type) == (new_kind.head.name, new_kind.head.type)
            ):
                self.schema_change(
                    operation,
                    location,
                    path,
                    'changed',
                    'type',
                    'recursionHead',
                    _shape_name(old_kind),
                    _shape_name(new_kind),
                    'other',
                    'review',
                )
            return
        if isinstance(old_kind, JsonShape) and isinstance(new_kind, JsonShape):
            self.schema_scalar(
                operation, location, path, 'type', 'schema', old_kind.raw, new_kind.raw, 'other', 'review'
            )
            return
        ancestors[old] = new
        try:
            self.shape_facets(operation, location, path, old, new, direction)
            if isinstance(old_kind, ObjectShape) and isinstance(new_kind, ObjectShape):
                self.object_shape(operation, location, path, old_kind, new_kind, direction, ancestors)
            elif isinstance(old_kind, ArrayShape) and isinstance(new_kind, ArrayShape):
                self.optional_shape_pair(
                    operation,
                    location,
                    (*path, ItemsSegment()),
                    old_kind.items,
                    new_kind.items,
                    direction,
                    ancestors,
                )
            elif isinstance(old_kind, UnionShape) and isinstance(new_kind, UnionShape):
                self.union_shape(operation, location, path, old_kind, new_kind, direction, ancestors)
        finally:
            del ancestors[old]

    def shape_facets(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: BaseShape,
        new: BaseShape,
        direction: Direction,
    ) -> None:
        old_facets = {name: _literal(facet.value) for name, facet in facets_of(old.shape)}
        new_facets = {name: _literal(facet.value) for name, facet in facets_of(new.shape)}
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            old_facets.pop('format', None)
            new_facets.pop('format', None)
        for name in sorted(old_facets.keys() | new_facets.keys()):
            before, after = old_facets.get(name), new_facets.get(name)
            if before == after:
                continue
            rule, impact = _facet_rule(direction, name, before, after)
            self.schema_change(operation, location, path, 'changed', 'constraint', name, before, after, rule, impact)
        before_enum, after_enum = _enum(old), _enum(new)
        if before_enum != after_enum:
            # No `attribute`, for the same reason a property change carries none: the
            # members that left and arrived are the change, and `enum` would be the
            # container they left. `kind` says which side each row is stating, so the
            # renderer never has to read an empty `after` as "the enum is gone".
            removed, added = _enum_delta(before_enum, after_enum)
            if removed:
                self.schema_change(
                    operation,
                    location,
                    path,
                    'removed',
                    'enum-value',
                    None,
                    removed,
                    None,
                    f'{direction}-enum-value-removed',
                    'breaking' if direction == 'request' else 'compatible',
                )
            if added:
                self.schema_change(
                    operation,
                    location,
                    path,
                    'added',
                    'enum-value',
                    None,
                    None,
                    added,
                    f'{direction}-enum-value-added',
                    'compatible' if direction == 'request' else 'review',
                )
        self.schema_scalar(
            operation,
            location,
            path,
            'documentation',
            'displayName',
            _facet(old.display_name),
            _facet(new.display_name),
            'documentation-changed',
            'cosmetic',
        )
        self.schema_scalar(
            operation,
            location,
            path,
            'documentation',
            'description',
            _facet(old.description),
            _facet(new.description),
            'documentation-changed',
            'cosmetic',
        )
        self.schema_scalar(
            operation, location, path, 'custom-facet', 'customFacets', _custom(old), _custom(new), 'other', 'review'
        )
        if isinstance(old.shape, FileShape) and isinstance(new.shape, FileShape):
            self.schema_scalar(
                operation,
                location,
                path,
                'constraint',
                'fileTypes',
                _facet_list(old.shape.file_types),
                _facet_list(new.shape.file_types),
                'other',
                'review',
            )
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            before = _facet(old.shape.format) or 'rfc3339'
            after = _facet(new.shape.format) or 'rfc3339'
            self.schema_scalar(
                operation, location, path, 'constraint', 'format', before, after, 'format-changed', 'breaking'
            )

    def object_shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: ObjectShape,
        new: ObjectShape,
        direction: Direction,
        ancestors: dict[BaseShape, BaseShape],
    ) -> None:
        old_properties, new_properties = old.properties or {}, new.properties or {}
        for name, before in old_properties.items():
            child_path = (*path, PropertySegment(name))
            after = new_properties.get(name)
            if after is None:
                rule = f'{direction}-property-removed'
                impact: Impact = 'breaking' if direction == 'response' else 'review'
                self.schema_change(
                    operation,
                    location,
                    child_path,
                    'removed',
                    'property',
                    None,
                    _property(before),
                    None,
                    rule,
                    impact,
                )
                continue
            self.required(operation, location, child_path, before.required, after.required, direction)
            self.shape(operation, location, child_path, before.base, after.base, direction, ancestors)
        for name, after in new_properties.items():
            if name in old_properties:
                continue
            child_path = (*path, PropertySegment(name))
            if direction == 'request' and after.required:
                rule, impact = 'request-property-added-required', 'breaking'
            else:
                rule, impact = f'{direction}-property-added', 'compatible'
            self.schema_change(
                operation, location, child_path, 'added', 'property', None, None, _property(after), rule, impact
            )
        old_patterns, new_patterns = old.pattern_properties or {}, new.pattern_properties or {}
        common = old_patterns.keys() & new_patterns.keys()
        old_order = tuple(name for name in old_patterns if name in common)
        new_order = tuple(name for name in new_patterns if name in common)
        if old_order != new_order:
            self.schema_change(
                operation,
                location,
                path,
                'changed',
                'pattern-property',
                'order',
                old_order,
                new_order,
                'other',
                'review',
            )
        for pattern, pattern_before in old_patterns.items():
            child_path = (*path, PatternPropertySegment(pattern))
            pattern_after = new_patterns.get(pattern)
            if pattern_after is None:
                rule = f'{direction}-constraint-loosened'
                pattern_impact: Impact = 'compatible' if direction == 'request' else 'breaking'
                self.schema_change(
                    operation,
                    location,
                    child_path,
                    'removed',
                    'pattern-property',
                    'pattern',
                    pattern_before.base.type,
                    None,
                    rule,
                    pattern_impact,
                )
                continue
            self.shape(operation, location, child_path, pattern_before.base, pattern_after.base, direction, ancestors)
        for pattern, pattern_after in new_patterns.items():
            if pattern in old_patterns:
                continue
            rule = f'{direction}-constraint-tightened'
            pattern_impact = 'breaking' if direction == 'request' else 'compatible'
            self.schema_change(
                operation,
                location,
                (*path, PatternPropertySegment(pattern)),
                'added',
                'pattern-property',
                'pattern',
                None,
                pattern_after.base.type,
                rule,
                pattern_impact,
            )

    def union_shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: UnionShape,
        new: UnionShape,
        direction: Direction,
        ancestors: dict[BaseShape, BaseShape],
    ) -> None:
        old_members, new_members = old.any_of or [], new.any_of or []
        buckets: dict[tuple[str, str], deque[tuple[int, BaseShape]]] = defaultdict(deque)
        # Fingerprinted once each, not once per old member the bucket is searched for:
        # the search below is linear in the bucket, and every probe walks a whole
        # member subtree, so computing them inside it squares a deep traversal.
        new_fingerprints: dict[int, object] = {}
        for index, member in enumerate(new_members):
            buckets[_member_key(member)].append((index, member))
            new_fingerprints[index] = _shape_fingerprint(member)
        matched: set[int] = set()
        old_occurrences: dict[tuple[str, str], int] = defaultdict(int)
        for before in old_members:
            key = _member_key(before)
            candidates = buckets[key]
            old_occurrences[key] += 1
            segment = UnionMemberSegment(before.name, before.type, old_occurrences[key])
            if not candidates:
                self.schema_change(
                    operation,
                    location,
                    (*path, segment),
                    'removed',
                    'union-member',
                    None,
                    _shape_described(before),
                    None,
                    f'{direction}-enum-value-removed',
                    'breaking' if direction == 'request' else 'compatible',
                )
                continue
            fingerprint = _shape_fingerprint(before)
            exact = next((candidate for candidate in candidates if new_fingerprints[candidate[0]] == fingerprint), None)
            if exact is None:
                new_index, after = candidates.popleft()
            else:
                candidates.remove(exact)
                new_index, after = exact
            matched.add(new_index)
            self.shape(operation, location, (*path, segment), before, after, direction, ancestors)
        new_occurrences: dict[tuple[str, str], int] = defaultdict(int)
        for index, after in enumerate(new_members):
            key = _member_key(after)
            new_occurrences[key] += 1
            if index in matched:
                continue
            self.schema_change(
                operation,
                location,
                (*path, UnionMemberSegment(after.name, after.type, new_occurrences[key])),
                'added',
                'union-member',
                None,
                None,
                _shape_described(after),
                f'{direction}-enum-value-added',
                'compatible' if direction == 'request' else 'review',
            )

    def optional_shape_pair(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: BaseShape | None,
        new: BaseShape | None,
        direction: Direction,
        ancestors: dict[BaseShape, BaseShape],
    ) -> None:
        if old is None or new is None:
            self.optional_shape(operation, location, old, new, direction, path)
        else:
            self.shape(operation, location, path, old, new, direction, ancestors)

    def required(  # noqa: PLR0913, PLR0917 - compares the two required flags at one coordinate
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: bool,  # noqa: FBT001 - the old facet value
        new: bool,  # noqa: FBT001 - the new facet value
        direction: Direction,
    ) -> None:
        if old == new:
            return
        became = 'required' if new else 'optional'
        rule = f'{direction}-property-{became}'
        impact: Impact = 'breaking' if (direction == 'request') == new else 'compatible'
        self.schema_change(operation, location, path, 'changed', 'required', 'required', old, new, rule, impact)

    def schema_scalar(  # noqa: PLR0913, PLR0917 - a scalar delta plus its schema coordinate and policy
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        subject: Subject,
        attribute: str,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        if before != after:
            self.schema_change(operation, location, path, 'changed', subject, attribute, before, after, rule, impact)

    def schema_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        operation: OperationId | None,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        kind: ChangeKind,
        subject: Subject,
        attribute: str | None,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        if operation is None:
            # No operation owner means the walk started at `api_parameters`, the only
            # API-scoped shape RAML has. Narrowed by check rather than by `cast`: a
            # second no-owner caller over a body would otherwise file a body change
            # under `ParameterLocation` and say nothing about it.
            if not isinstance(location, ParameterLocation):
                raise AssertionError(f'an API-scoped shape change needs a parameter location, not {location!r}')
            self.api_schema_change(location, path, kind, subject, attribute, before, after, rule, impact)
        else:
            self.changes.append(
                SchemaChanged(operation, location, path, kind, subject, attribute, before, after, impact, rule)
            )

    def api_schema_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        location: ParameterLocation,
        path: tuple[PathSegment, ...],
        kind: ChangeKind,
        subject: Subject,
        attribute: str | None,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        self.changes.append(ApiSchemaChanged(location, path, kind, subject, attribute, before, after, impact, rule))


def _operations(raml: Raml) -> dict[OperationId, tuple[EndPoint, Operation]]:
    return {
        OperationId(endpoint.full_uri, method): (endpoint, operation)
        for endpoint in raml.endpoints.values()
        for method, operation in endpoint.operations.items()
    }


def _protocol_rule(old: tuple[str, ...], new: tuple[str, ...]) -> tuple[str, Impact]:
    removed = tuple(item for item in old if item not in new)
    return ('protocol-removed', 'breaking') if removed else ('protocol-added', 'compatible')


def _api_protocols(api: APIFragment | None) -> tuple[str, ...]:
    return () if api is None else tuple(facet.value for facet in api.protocols)


def _facet_rule(direction: Direction, attribute: str, before: object, after: object) -> tuple[str, Impact]:
    if attribute == 'format' and (before in DATETIME_FORMATS or after in DATETIME_FORMATS):
        return 'format-changed', 'breaking'
    loosened = _loosened(attribute, before, after)
    if loosened is None:
        return 'other', 'review'
    move = 'loosened' if loosened else 'tightened'
    impact: Impact = 'breaking' if (direction == 'request') != loosened else 'compatible'
    return f'{direction}-constraint-{move}', impact


_UPPER: Final = frozenset({'maxItems', 'maxLength', 'maxProperties', 'maximum'})
_LOWER: Final = frozenset({'minItems', 'minLength', 'minProperties', 'minimum'})
_RESTRICTING: Final = frozenset({'pattern', 'format', 'multipleOf', 'uniqueItems', 'additionalProperties'})


def _loosened(attribute: str, before: object, after: object) -> bool | None:  # noqa: PLR0911, PLR0912
    if attribute in _RESTRICTING:
        if attribute in ('uniqueItems', 'additionalProperties'):
            restrictive = attribute == 'uniqueItems'
            was, now = before is restrictive, after is restrictive
            return None if was == now else not now
        if before is None:
            return False if after is not None else None
        if after is None:
            return True
        if attribute == 'multipleOf':
            try:
                old, new = Fraction(str(before)), Fraction(str(after))
            except (ValueError, ZeroDivisionError):
                return None
            if old <= 0 or new <= 0:
                return None
            if (new / old).denominator == 1:
                return False
            if (old / new).denominator == 1:
                return True
        if attribute == 'format':
            old_width = INTEGER_FORMATS.get(str(before), _NUMBER_WIDTH.get(str(before)))
            new_width = INTEGER_FORMATS.get(str(after), _NUMBER_WIDTH.get(str(after)))
            return None if old_width is None or new_width is None else new_width > old_width
        return None
    old_number, new_number = _numeric(before), _numeric(after)
    if old_number is None and new_number is None:
        return None
    if old_number is None:
        return False
    if new_number is None:
        return True
    return new_number > old_number if attribute in _UPPER else new_number < old_number


def _numeric(value: object) -> Fraction | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def _change_row(  # noqa: PLR0913 - one flag per column the table decided to carry
    entry: _Entry,
    *,
    side_stated: bool,
    paths: bool,
    change: bool,
    subjects: bool,
    details: bool,
    described: bool,
    operations: bool,
) -> str:
    """One row shape for all four results: they differ in owner, not in reading.

    Every column is optional except `Where` and `Compatibility`, and the table
    decides which it carries by asking whether any of its rows fills one. `Path`
    is empty whenever the change is to the coordinate itself -- both for a
    contract change, which has no path, and for a shape change at the root, whose
    `$` says "the thing in Where". A path appears when it reaches *inside*, and
    then it is anchored at `$` because `$.title` needs somewhere to hang.
    """
    result = entry.change
    cells = [_cell(_location_label(result.location, side_stated=side_stated))]
    if paths:
        inside = ''
        if isinstance(result, (ApiSchemaChanged, SchemaChanged)) and result.path:
            inside = _cell(_inline_code(_path_label(result.path)))
        cells.append(inside)
    if change:
        cells.append(_cell(_change_label(result)))
    if subjects:
        cells.append(result.subject.replace('-', ' ').capitalize())
    if details:
        cells.append(_cell(_detail(result)))
    if described:
        cells.append(_cell(_plain_inline(_summary(_description(result)))))
    cells.append(result.impact.title())
    if operations:
        cells.append(
            ', '.join(_inline_code(f'{operation.method.upper()} {operation.path}') for operation in entry.operations)
        )
    return f'| {" | ".join(cells)} |'


def _description(change: LocatedChange) -> str:
    """What the author said the added or removed thing is for, if anything.

    Its own column rather than folded into `Detail`, because a type and a
    requiredness are what to send and this is what it means -- a reader scanning
    a list of new fields wants the second without re-reading the first. It is the
    one thing an addition can say that its coordinate cannot, and it reaches every
    entity RAML lets an author describe: properties, query parameters, headers,
    response statuses, bodies, union members and security schemes.
    """
    value = change.after if change.after is not None else change.before
    return str(value['description']) if isinstance(value, dict) and 'description' in value else ''


def _detail(change: LocatedChange) -> str:
    """The values, in one cell: a transition for a change, a descriptor otherwise.

    `Before` and `After` fitted a `changed` row and misfitted the other two,
    where one side is empty by construction and the heading already says which.
    A removed body, response or union member fills neither, because its
    coordinate is the whole fact -- and a table of those drops this column too.
    """
    before = _markdown_value(change, change.before) if change.before is not None else ''
    after = _markdown_value(change, change.after) if change.after is not None else ''
    if change.kind != 'changed':
        return before or after
    return f'{before or "Absent"} -> {after or "Absent"}'


def _operation_markdown(change: OperationAdded | OperationRemoved) -> str:
    parts = [_inline_code(f'{change.operation.method.upper()} {change.operation.path}')]
    if change.display_name is not None:
        parts.append(f'**{_plain_inline(_summary(change.display_name))}**')
    if change.description is not None:
        parts.append(_plain_inline(_summary(change.description)))
    return '- ' + ' - '.join(parts)


def _location_label(  # noqa: PLR0911 - one spelling per location variant
    location: ApiLocation | OperationLocation, *, side_stated: bool = False
) -> str:
    """Where the change is, without the part the reader has already been told.

    `side_stated` is true under a **Request** or **Response** heading, which has
    named the side for every row beneath it. Repeating it turned each cell into
    "Response `200` header `X-Trace`" under a section already headed Response --
    the word twice on one line, and the column's widest cells spent on it.

    A response keeps its status, which the heading does not state. A request body
    keeps nothing but the media type, which is all that was ever distinguishing.
    """
    side = '' if side_stated else 'Response '
    if isinstance(location, RequestBody):
        return f'{"Body" if side_stated else "Request body"} {_inline_code(location.media_type)}'
    if isinstance(location, ResponseBody):
        return f'{side}{_inline_code(location.status)} body {_inline_code(location.media_type)}'
    if isinstance(location, ResponseStatus):
        return f'{"Status" if side_stated else "Response"} {_inline_code(location.status)}'
    if isinstance(location, ParameterLocation):
        prefix = f'{side}{_inline_code(location.response_status)} ' if location.response_status else ''
        # A header is a header; only the other three bindings are RAML "parameters".
        noun = 'header' if location.binding == 'header' else f'{location.binding} parameter'
        return f'{prefix}{noun} {_inline_code(location.name)}'
    if isinstance(location, SecurityLocation):
        return 'Security'
    if isinstance(location, TransportLocation):
        return 'Transport'
    return 'Operation'


def _path_label(path: tuple[PathSegment, ...]) -> str:
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


def _change_label(change: LocatedChange) -> str:
    """The Change column: an attribute is an identifier, a kind is prose.

    Every `attribute` is a name -- a RAML facet, a security setting, a node the
    author wrote -- so all of them read as code and none of them read as a
    sentence. That keeps one column able to hold `maxLength` next to
    `Property removed` without the reader deciding which is which.
    """
    if change.attribute == 'required':
        # "Requiredness" is the property's own word. Under `security` the thing
        # that became required is a credential, and naming it the same way left a
        # reader to guess what was optional before.
        return 'Authentication' if change.subject == 'security' else 'Requiredness'
    if change.attribute is not None and change.kind == 'changed':
        return _inline_code(change.attribute)
    return f'{change.subject.replace("-", " ").capitalize()} {change.kind}'


def _value(subject: Subject, value: object) -> str:
    """One value of `subject`, spelled the way the RAML document spells it.

    Dispatching on `subject` and not on the Python type is the point. `True` is
    "Required" under `required` and `true` under `constraint`, and a renderer that
    reads the runtime type alone cannot tell those apart -- it reported
    `additionalProperties: true -> false` as "Required -> Optional".
    """
    if value is None:
        return 'Absent'
    if isinstance(value, bool):
        if subject in ('required', 'security'):
            return 'Required' if value else 'Optional'
        return 'true' if value else 'false'
    if isinstance(value, dict):
        # A descriptor carries any subset of name, requiredness and type, and its
        # `description` belongs to its own column. Missing keys are not defaults:
        # a removed body states no requiredness because a body has none.
        parts = [str(value['name'])] if 'name' in value else []
        parts.extend(['required' if value['required'] else 'optional'] if 'required' in value else [])
        parts.extend([str(value['type'])] if 'type' in value else [])
        return ' '.join(parts)
    if isinstance(value, tuple):
        return ', '.join(_scalar(item) for item in value) or 'None'
    return _scalar(value)


def _scalar(value: object) -> str:
    """A leaf as RAML spells it: `true`, `false` and `null`, not Python's."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _markdown_value(change: LocatedChange, value: object) -> str:
    # An added or removed subject has one populated side by construction, and the
    # label already says which. Printing "Absent" opposite it states the same fact
    # twice for a property, and states a false one for an enum member, whose empty
    # side means "no members arrived" rather than "the enum is gone".
    if value is None and change.kind != 'changed':
        return ''
    rendered = _value(change.subject, value)
    rendered = _summary(rendered) if change.attribute == 'description' else rendered
    return _plain_inline(rendered)


def _cell(value: str) -> str:
    return ' '.join(value.splitlines()).replace('\t', ' ').replace('|', '\\|')


def _verdict_sentence(counts: Mapping[Impact, int]) -> str:
    if counts['breaking']:
        count = counts['breaking']
        return f'{count} breaking change{"s" if count != 1 else ""} require action before release.'
    if counts['review']:
        count = counts['review']
        return f'{count} change{"s" if count != 1 else ""} require manual review.'
    return 'No existing caller is broken by the reported changes.'


def _location_record(location: OperationLocation) -> dict[str, object]:
    return {'kind': type(location).__name__, **asdict(location)}


def _segment_record(segment: PathSegment) -> dict[str, object]:
    return {'kind': type(segment).__name__, **asdict(segment)}


def _facet(value: object | None) -> object | None:
    return getattr(value, 'value', None)


def _text_facet(value: object | None) -> str | None:
    raw = _facet(value)
    return raw if isinstance(raw, str) else None


_SUMMARY_LIMIT: Final = 160
_PLAIN_MARKDOWN_ESCAPES: Final = str.maketrans({char: f'\\{char}' for char in r'\\`*_{}[]<>'})


def _summary(value: str) -> str:
    lines = [line.strip() for line in value.splitlines()]
    nonempty = [line for line in lines if line]
    if not nonempty:
        return ''
    first = nonempty[0]
    truncated = len(nonempty) > 1 or len(first) > _SUMMARY_LIMIT
    if len(first) > _SUMMARY_LIMIT:
        first = first[: _SUMMARY_LIMIT - 3].rstrip()
    return first + ('...' if truncated else '')


def _plain_inline(value: str) -> str:
    return value.translate(_PLAIN_MARKDOWN_ESCAPES)


def _inline_code(value: str) -> str:
    value = ' '.join(value.splitlines())
    fence = '`'
    while fence in value:
        fence += '`'
    padding = ' ' if value.startswith(('`', ' ')) or value.endswith(('`', ' ')) else ''
    return f'{fence}{padding}{value}{padding}{fence}'


def _literal(value: object) -> object:
    if isinstance(value, Fraction):
        return _fraction_text(value)
    pattern = getattr(value, 'pattern', None)
    return pattern if isinstance(pattern, str) else value


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    residue = value.denominator
    for factor in (2, 5):
        while residue % factor == 0:
            residue //= factor
    if residue != 1:
        return f'{value.numerator}/{value.denominator}'
    digits, scaled = 0, value
    while scaled.denominator != 1:
        scaled *= 10
        digits += 1
    text = str(abs(scaled.numerator)).rjust(digits + 1, '0')
    return f'{"-" if scaled.numerator < 0 else ""}{text[:-digits]}.{text[-digits:]}'


def _enum(base: BaseShape) -> tuple[object, ...] | None:
    return None if base.enum is None else tuple(node.raw for node in base.enum)


def _enum_delta(
    before: tuple[object, ...] | None, after: tuple[object, ...] | None
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    old_keys = {_typed_key(value) for value in before or ()}
    new_keys = {_typed_key(value) for value in after or ()}
    removed = tuple(value for value in before or () if _typed_key(value) not in new_keys)
    added = tuple(value for value in after or () if _typed_key(value) not in old_keys)
    return removed, added


def _typed_key(value: object) -> object:
    if isinstance(value, dict):
        return ('dict', tuple((key, _typed_key(child)) for key, child in value.items()))
    if isinstance(value, list):
        return ('list', tuple(_typed_key(child) for child in value))
    return (type(value).__name__, value)


def _custom(base: BaseShape) -> tuple[tuple[str, str], ...] | None:
    return (
        None if not base.custom_facets else tuple((name, repr(node.raw)) for name, node in base.custom_facets.items())
    )


def _facet_list(values: Iterable[object] | None) -> tuple[object, ...] | None:
    return None if values is None else tuple(getattr(value, 'value', value) for value in values)


def _property(prop: object) -> dict[str, object] | None:
    base = getattr(prop, 'base', None)
    return _descriptor(
        getattr(base, 'description', None),
        type_name=getattr(base, 'type', ''),
        required=bool(getattr(prop, 'required', False)),
    )


def _parameter(param: Parameter) -> dict[str, object] | None:
    return _descriptor(param.base.description, type_name=param.base.type, required=param.required)


def _scheme(scheme: SecurityScheme) -> dict[str, object] | None:
    """An alternative names itself -- `SecurityLocation` is empty -- and says what
    the credential is, which is what a caller needs to go and obtain one.
    """
    definition = scheme.definition
    return _descriptor(None if definition is None else definition.description, name=scheme.name)


def _shape_described(shape: BaseShape | None) -> dict[str, object] | None:
    """A body or union member: its coordinate names it, so only the prose is new."""
    return None if shape is None else _descriptor(shape.description)


def _descriptor(
    description: object, *, type_name: object = None, required: bool | None = None, name: str | None = None
) -> dict[str, object] | None:
    """What an added or removed entity *is*, beyond what its coordinate says.

    A type and a requiredness say what to send or expect. The author's own
    `description` says what the thing is *for*, which is the question a reader
    has about anything they have not seen before -- a new query parameter and a
    new response status as much as a new property.

    Every key is optional, so this stays inside the rule that nothing restates
    its coordinate: a removed body names no type, because `Where` already did,
    and carries only whatever prose the author wrote about it. All-empty returns
    `None`, and the table drops the column nobody filled.
    """
    text = _text_facet(description)
    descriptor: dict[str, object] = {}
    if name is not None:
        descriptor['name'] = name
    if type_name is not None:
        descriptor['type'] = type_name
    if required is not None:
        descriptor['required'] = required
    if text:
        descriptor['description'] = text
    return descriptor or None


def _member_key(base: BaseShape) -> tuple[str, str]:
    return base.type, base.name or ''


def _shape_fingerprint(base: BaseShape, seen: set[BaseShape] | None = None) -> object:  # noqa: PLR0911
    if seen is None:
        seen = set()
    if base in seen:
        return ('recursive', base.name, base.type)
    seen.add(base)
    try:
        shape = base.shape
        common = (
            type(shape).__name__,
            base.name,
            base.type,
            tuple((name, _typed_key(_literal(facet.value))) for name, facet in facets_of(shape)),
            tuple(_typed_key(value) for value in _enum(base) or ()),
            _facet(base.display_name),
            _facet(base.description),
            _custom(base),
        )
        if isinstance(shape, ObjectShape):
            properties = tuple(
                sorted(
                    (name, prop.required, _shape_fingerprint(prop.base, seen))
                    for name, prop in (shape.properties or {}).items()
                )
            )
            patterns = tuple(
                (name, _shape_fingerprint(prop.base, seen)) for name, prop in (shape.pattern_properties or {}).items()
            )
            return common, properties, patterns
        if isinstance(shape, ArrayShape):
            return common, None if shape.items is None else _shape_fingerprint(shape.items, seen)
        if isinstance(shape, UnionShape):
            members = tuple(sorted((_shape_fingerprint(member, seen) for member in shape.any_of or []), key=repr))
            return common, members
        if isinstance(shape, RecursiveShape):
            return common, shape.head.name, shape.head.type
        if isinstance(shape, JsonShape):
            return common, _typed_key(shape.raw)
        return common
    finally:
        seen.remove(base)


def _shape_name(shape: object) -> str:
    return shape.head.name or shape.head.type if isinstance(shape, RecursiveShape) else type(shape).__name__


def _scheme_definition(scheme: SecurityScheme) -> dict[str, object]:
    if scheme.definition is None:
        return {}
    definition = scheme.definition
    settings = definition.settings
    found: dict[str, object] = {'type': definition.type}
    if settings is not None:
        found.update((name, facet.value) for name, facet in settings.values.items())
        found.update((name, tuple(items)) for name, items in settings.lists.items())
    return found


def _matches(change: Change, match: CompatibilityMatch | None) -> bool:
    if match is None:
        return True
    operation = _operation_of(change)
    if match.operation is not None and (operation is None or match.operation.search(operation) is None):
        return False
    location, path, subject, attribute, before, after = _match_fields(change)
    return (
        (match.location is None or match.location == location)
        and (match.path is None or match.path == path)
        and (match.subject is None or match.subject == subject)
        and (match.attribute is None or match.attribute == attribute)
        and (not match.before_set or _normal(before) == _normal(match.before))
        and (not match.after_set or _normal(after) == _normal(match.after))
    )


def _operation_of(change: Change) -> str | None:
    if isinstance(change, (ApiChanged, ApiSchemaChanged)):
        return None
    return f'{change.operation.method.upper()} {change.operation.path}'


def _match_fields(change: Change) -> tuple[str, str | None, str, str | None, object, object]:
    if isinstance(change, ApiChanged):
        return 'Api', None, change.subject, change.attribute, change.before, change.after
    if isinstance(change, (OperationAdded, OperationRemoved)):
        # No `attribute` and no values. An operation that arrived or left has no
        # field that took a new value -- `rule` already separates `entity-added`
        # from `entity-removed`, and `match.operation` names which one. Inventing
        # an `availability: present` to match on would only be matchable by
        # someone who had read this function.
        return 'Operation', None, 'operation', None, None, None
    return (
        type(change.location).__name__,
        _path_label(change.path) if isinstance(change, (ApiSchemaChanged, SchemaChanged)) else None,
        change.subject,
        change.attribute,
        change.before,
        change.after,
    )


def _normal(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _normal(child)) for key, child in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normal(child) for child in value)
    return value
