"""Operation-local backward compatibility over two effective RAML models."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from typing import TYPE_CHECKING, Final, Literal, Protocol

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
    'BackwardChange',
    'ItemsSegment',
    'OperationAdded',
    'OperationChanged',
    'OperationId',
    'OperationRemoved',
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
type SchemaLocation = RequestBody | ResponseBody | ParameterLocation


@dataclass(frozen=True, slots=True)
class PropertySegment:
    name: str


@dataclass(frozen=True, slots=True)
class ItemsSegment:
    pass


@dataclass(frozen=True, slots=True)
class UnionMemberSegment:
    name: str | None
    type: str
    occurrence: int = 1


type PathSegment = PropertySegment | ItemsSegment | UnionMemberSegment


class BackwardChange(Protocol):
    impact: Impact
    rule: str


@dataclass(frozen=True, slots=True)
class ApiChanged:
    kind: ChangeKind
    subject: str
    attribute: str
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
class OperationChanged:
    operation: OperationId
    location: OperationLocation
    kind: ChangeKind
    subject: str
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
    subject: str
    attribute: str | None
    before: object
    after: object
    impact: Impact
    rule: str


type Change = ApiChanged | OperationAdded | OperationRemoved | OperationChanged | SchemaChanged

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


def render_markdown(changes: Sequence[Change]) -> str:
    counts = {impact: sum(change.impact == impact for change in changes) for impact in _IMPACTS}
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
    ]
    api_changes = [change for change in changes if isinstance(change, ApiChanged)]
    added = [change for change in changes if isinstance(change, OperationAdded)]
    removed = [change for change in changes if isinstance(change, OperationRemoved)]
    if api_changes or added or removed:
        lines.extend(('', '## API surface'))
    if api_changes:
        lines.extend(('', '### API contract', ''))
        lines.extend(('| Contract | Change | Before | After | Compatibility |', '|---|---|---|---|---|'))
        lines.extend(_api_markdown_row(change) for change in api_changes)
    if removed:
        lines.extend(('', '### Removed operations', ''))
        lines.extend(_operation_markdown(change) for change in removed)
    if added:
        lines.extend(('', '### Added operations', ''))
        lines.extend(_operation_markdown(change) for change in added)
    grouped: dict[OperationId, tuple[list[OperationChanged], list[SchemaChanged]]] = {}
    for change in changes:
        if not isinstance(change, (OperationChanged, SchemaChanged)):
            continue
        method_changes, schema_changes = grouped.setdefault(change.operation, ([], []))
        if isinstance(change, OperationChanged):
            method_changes.append(change)
        else:
            schema_changes.append(change)
    for operation, (method_changes, schema_changes) in grouped.items():
        lines.extend(('', f'## {_inline_code(f"{operation.method.upper()} {operation.path}")}', ''))
        if method_changes:
            lines.extend(('### Method contract', '', '| Contract | Change | Before | After | Compatibility |'))
            lines.append('|---|---|---|---|---|')
            lines.extend(_operation_change_row(change) for change in method_changes)
        if schema_changes:
            lines.extend(('', '### Schemas', '', '| Schema | Path | Change | Before | After | Compatibility |'))
            lines.append('|---|---|---|---|---|---|')
            lines.extend(_schema_change_row(change) for change in schema_changes)
    return '\n'.join(lines) + '\n'


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
        for operation_id in new_operations.keys() - old_operations.keys():
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
        old_protocols = tuple(old.protocols) or _api_protocols(self.old_api)
        new_protocols = tuple(new.protocols) or _api_protocols(self.new_api)
        self.protocols(operation_id, old_protocols, new_protocols)
        self.parameters(operation_id, old_endpoint.uri_parameters, new_endpoint.uri_parameters, 'path')
        self.security(operation_id, old.secured_by, new.secured_by)
        self.request(operation_id, old.request, new.request)
        self.responses(operation_id, old.responses, new.responses)
        self.operation_scalar(
            operation_id,
            OperationContract(),
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
            self.changes.append(ApiChanged('changed', 'base-uri', 'baseUri', old, new, 'breaking', 'base-uri-changed'))

    def protocols(self, operation: OperationId, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        if old == new:
            return
        removed = tuple(item for item in old if item not in new)
        rule = 'protocol-removed' if removed else 'protocol-added'
        impact: Impact = 'breaking' if removed else 'compatible'
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
        operation: OperationId,
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
                    'present',
                    'absent',
                    'entity-removed',
                    'breaking',
                )
                continue
            self.parameters(operation, before.headers, after.headers, 'header', response_status=status)
            self.bodies(operation, before.bodies, after.bodies, 'response', status=status)
            self.operation_scalar(
                operation,
                ResponseStatus(status),
                'description',
                _facet(before.description),
                _facet(after.description),
                'documentation-changed',
                'cosmetic',
            )
        for status in new.keys() - old.keys():
            self.operation_change(
                operation,
                ResponseStatus(status),
                'added',
                'response',
                None,
                'absent',
                'present',
                'entity-added',
                'compatible',
            )

    def parameters(
        self,
        operation: OperationId,
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
        operation: OperationId,
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
                    'mediaType',
                    media_type,
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
                'mediaType',
                None,
                media_type,
                'entity-added',
                'compatible',
            )

    def security(self, operation: OperationId, old: list[SecurityScheme], new: list[SecurityScheme]) -> None:
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
                    name,
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
        for name in new_by_name.keys() - old_by_name.keys():
            self.operation_change(
                operation,
                location,
                'added',
                'security-alternative',
                'name',
                None,
                name,
                'security-alternative-added',
                'compatible',
            )

    def security_description(
        self,
        operation: OperationId,
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
        operation: OperationId,
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
        operation: OperationId,
        location: OperationLocation,
        attribute: str,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        if before != after:
            self.operation_change(operation, location, 'changed', attribute, attribute, before, after, rule, impact)

    def operation_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        operation: OperationId,
        location: OperationLocation,
        kind: ChangeKind,
        subject: str,
        attribute: str | None,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        self.changes.append(
            OperationChanged(operation, location, kind, subject, attribute, before, after, impact, rule)
        )

    def optional_shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId,
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
        operation: OperationId,
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
            self.schema_scalar(operation, location, path, 'schema', old_kind.raw, new_kind.raw, 'other', 'review')
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
        operation: OperationId,
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
            rule, impact = _enum_rule(direction, before_enum, after_enum)
            self.schema_change(
                operation, location, path, 'changed', 'enum', 'enum', before_enum, after_enum, rule, impact
            )
        self.schema_scalar(
            operation,
            location,
            path,
            'description',
            _facet(old.description),
            _facet(new.description),
            'documentation-changed',
            'cosmetic',
        )
        self.schema_scalar(operation, location, path, 'customFacets', _custom(old), _custom(new), 'other', 'review')
        if isinstance(old.shape, FileShape) and isinstance(new.shape, FileShape):
            self.schema_scalar(
                operation,
                location,
                path,
                'fileTypes',
                _facet_list(old.shape.file_types),
                _facet_list(new.shape.file_types),
                'other',
                'review',
            )
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            before = _facet(old.shape.format) or 'rfc3339'
            after = _facet(new.shape.format) or 'rfc3339'
            self.schema_scalar(operation, location, path, 'format', before, after, 'format-changed', 'breaking')

    def object_shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId,
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

    def union_shape(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        old: UnionShape,
        new: UnionShape,
        direction: Direction,
        ancestors: dict[BaseShape, BaseShape],
    ) -> None:
        old_members, new_members = old.any_of or [], new.any_of or []
        buckets: dict[tuple[str, str], deque[tuple[int, BaseShape]]] = defaultdict(deque)
        for index, member in enumerate(new_members):
            buckets[_member_key(member)].append((index, member))
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
                    before.type,
                    None,
                    f'{direction}-enum-value-removed',
                    'breaking' if direction == 'request' else 'compatible',
                )
                continue
            new_index, after = candidates.popleft()
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
                after.type,
                f'{direction}-enum-value-added',
                'compatible' if direction == 'request' else 'review',
            )

    def optional_shape_pair(  # noqa: PLR0913, PLR0917 - carries one contract coordinate through the walk
        self,
        operation: OperationId,
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
        operation: OperationId,
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
        operation: OperationId,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        attribute: str,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        if before != after:
            self.schema_change(operation, location, path, 'changed', attribute, attribute, before, after, rule, impact)

    def schema_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        operation: OperationId,
        location: SchemaLocation,
        path: tuple[PathSegment, ...],
        kind: ChangeKind,
        subject: str,
        attribute: str | None,
        before: object,
        after: object,
        rule: str,
        impact: Impact,
    ) -> None:
        self.changes.append(
            SchemaChanged(operation, location, path, kind, subject, attribute, before, after, impact, rule)
        )


def _operations(raml: Raml) -> dict[OperationId, tuple[EndPoint, Operation]]:
    return {
        OperationId(endpoint.full_uri, method): (endpoint, operation)
        for endpoint in raml.endpoints.values()
        for method, operation in endpoint.operations.items()
    }


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


def _enum_rule(
    direction: Direction, before: tuple[str, ...] | None, after: tuple[str, ...] | None
) -> tuple[str, Impact]:
    was, now = set(before or ()), set(after or ())
    removed = bool(was - now)
    move = 'removed' if removed else 'added'
    if direction == 'request':
        impact: Impact = 'breaking' if removed else 'compatible'
    else:
        impact = 'compatible' if removed else 'review'
    return f'{direction}-enum-value-{move}', impact


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


def _operation_change_row(change: OperationChanged) -> str:
    return (
        f'| {_cell(_location_label(change.location))} | {_cell(_change_label(change))} | '
        f'{_cell(_markdown_value(change, change.before))} | {_cell(_markdown_value(change, change.after))} | '
        f'{change.impact.title()} |'
    )


def _schema_change_row(change: SchemaChanged) -> str:
    return (
        f'| {_cell(_location_label(change.location))} | {_cell(_inline_code(_path_label(change.path)))} | '
        f'{_cell(_change_label(change))} | {_cell(_markdown_value(change, change.before))} | '
        f'{_cell(_markdown_value(change, change.after))} | {change.impact.title()} |'
    )


def _api_markdown_row(change: ApiChanged) -> str:
    return (
        f'| API | {change.attribute} | {_cell(_plain_inline(_value(change.before)))} | '
        f'{_cell(_plain_inline(_value(change.after)))} | '
        f'{change.impact.title()} |'
    )


def _operation_markdown(change: OperationAdded | OperationRemoved) -> str:
    parts = [_inline_code(f'{change.operation.method.upper()} {change.operation.path}')]
    if change.display_name is not None:
        parts.append(f'**{_plain_inline(_summary(change.display_name))}**')
    if change.description is not None:
        parts.append(_plain_inline(_summary(change.description)))
    return '- ' + ' - '.join(parts)


def _location_label(location: OperationLocation) -> str:  # noqa: PLR0911 - one spelling per location variant
    if isinstance(location, RequestBody):
        return f'Request body {_inline_code(location.media_type)}'
    if isinstance(location, ResponseBody):
        return f'Response {_inline_code(location.status)} body {_inline_code(location.media_type)}'
    if isinstance(location, ResponseStatus):
        return f'Response {_inline_code(location.status)}'
    if isinstance(location, ParameterLocation):
        prefix = f'Response {_inline_code(location.response_status)} ' if location.response_status else ''
        return f'{prefix}{location.binding} parameter {_inline_code(location.name)}'
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
        else:
            member = segment.name or segment.type
            occurrence = f'#{segment.occurrence}' if segment.occurrence > 1 else ''
            out += f'<{member}{occurrence}>'
    return out


def _change_label(change: OperationChanged | SchemaChanged) -> str:
    if change.subject in ('property', 'parameter'):
        return f'{change.subject.title()} {change.kind}'
    if change.attribute == 'required':
        return 'Requiredness'
    if change.subject == 'security-setting' and change.attribute is not None:
        return f'Setting {_inline_code(change.attribute)}'
    if change.attribute is not None:
        return change.attribute
    return f'{change.subject.replace("-", " ").capitalize()} {change.kind}'


def _value(value: object) -> str:
    if value is None:
        return 'Absent'
    if isinstance(value, bool):
        return 'Required' if value else 'Optional'
    if isinstance(value, dict):
        required = 'required' if value.get('required') else 'optional'
        return f'{required} {value.get("type", "type")}'
    if isinstance(value, tuple):
        return ', '.join(str(item) for item in value) or 'None'
    return str(value)


def _markdown_value(change: OperationChanged | SchemaChanged, value: object) -> str:
    rendered = _value(value)
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


def _enum(base: BaseShape) -> tuple[str, ...] | None:
    return None if base.enum is None else tuple(str(node.raw) for node in base.enum)


def _custom(base: BaseShape) -> tuple[tuple[str, str], ...] | None:
    return (
        None if not base.custom_facets else tuple((name, repr(node.raw)) for name, node in base.custom_facets.items())
    )


def _facet_list(values: Iterable[object] | None) -> tuple[object, ...] | None:
    return None if values is None else tuple(getattr(value, 'value', value) for value in values)


def _property(prop: object) -> dict[str, object]:
    return {
        'type': getattr(getattr(prop, 'base', None), 'type', ''),
        'required': bool(getattr(prop, 'required', False)),
    }


def _parameter(param: Parameter) -> dict[str, object]:
    return {'type': param.base.type, 'required': param.required}


def _member_key(base: BaseShape) -> tuple[str, str]:
    return base.type, base.name or ''


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
    if isinstance(change, ApiChanged):
        return None
    return f'{change.operation.method.upper()} {change.operation.path}'


def _match_fields(change: Change) -> tuple[str, str | None, str, str | None, object, object]:
    if isinstance(change, ApiChanged):
        return 'Api', None, change.subject, change.attribute, change.before, change.after
    if isinstance(change, OperationAdded):
        return 'Operation', None, 'operation', 'availability', None, 'present'
    if isinstance(change, OperationRemoved):
        return 'Operation', None, 'operation', 'availability', 'present', None
    return (
        type(change.location).__name__,
        _path_label(change.path) if isinstance(change, SchemaChanged) else None,
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
