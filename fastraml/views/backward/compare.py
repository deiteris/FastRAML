"""The walk: two effective API models in, a list of changes out.

One traversal of everything a caller can depend on -- transport, security, path
and query parameters, headers, bodies and every shape below them -- reaching the
result classes in `model` through two emitters. Nothing here renders, and nothing
here decides a grade: a rule id is chosen from what moved, and `impact_of` maps
it to what that does to a caller.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Final, Literal

from fastraml.parser.fragments import APIFragment
from fastraml.types.base import BaseShape, Parameter, facets_of
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import DATETIME_FORMATS, INTEGER_FORMATS, DateTimeShape, FileShape
from fastraml.views.backward.model import (
    ApiChanged,
    ApiSchemaChanged,
    Change,
    ChangeKind,
    Direction,
    ItemsSegment,
    OperationAdded,
    OperationChanged,
    OperationContract,
    OperationId,
    OperationLocation,
    OperationRemoved,
    ParameterLocation,
    PathSegment,
    PatternPropertySegment,
    PropertySegment,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SchemaChanged,
    SchemaLocation,
    SecurityLocation,
    Subject,
    TransportLocation,
    UnionMemberSegment,
    impact_of,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fastraml.parser.directives import SecurityScheme
    from fastraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
    from fastraml.parser.security import SecuritySchemeDescription
    from fastraml.registry import Raml

__all__ = ['backward']


def backward(old: Raml, new: Raml) -> list[Change]:
    """Compare the callable operations of two models parsed with ``unwrap=True``."""
    analyzer = _Backward(old, new)
    analyzer.run()
    return analyzer.changes


_NUMBER_WIDTH: Final = {'float': 0, 'double': 1}


@dataclass(frozen=True, slots=True)
class _Site:
    """One shape coordinate, and the side of the wire it faces.

    The owner, the contract location, the path into the shape and the direction
    travel together through every shape method and are never read apart, so they
    travel as one value and a descent is `site.at(segment)`.

    `operation is None` is the API scope -- `baseUriParameters`, the only shape
    RAML hangs off the root -- and `schema_change` is the single place that reads
    it, so the walk above it never branches on whose shape it is in.
    """

    operation: OperationId | None
    location: SchemaLocation
    direction: Direction
    path: tuple[PathSegment, ...] = ()

    def at(self, segment: PathSegment) -> _Site:
        """The same coordinate, one step further into the shape."""
        return _Site(self.operation, self.location, self.direction, (*self.path, segment))


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
        self.documentation(operation_id, OperationContract(), old, new)

    def base_uri(self) -> None:
        old = _facet(self.old_api.base_uri) if self.old_api is not None else None
        new = _facet(self.new_api.base_uri) if self.new_api is not None else None
        if old != new:
            self.operation_change(
                None, TransportLocation(), 'changed', 'base-uri', 'base-uri-changed', 'baseUri', old, new
            )

    def api_parameters(self) -> None:
        old = {} if self.old_api is None else self.old_api.base_uri_parameters
        new = {} if self.new_api is None else self.new_api.base_uri_parameters
        for name, before in old.items():
            after = new.get(name)
            site = _Site(None, ParameterLocation('baseUri', name), 'request')
            if after is None:
                self.schema_change(site, 'removed', 'parameter', 'request-property-removed', before=_parameter(before))
                continue
            # The same requiredness rule a property gets, and the same walk over the
            # shape: an API-scoped parameter differs from an operation's in who owns
            # it, which is the one thing `_Site` carries and nothing here re-decides.
            self.required(site, before.required, after.required)
            self.shape(site, before.base, after.base, {})
        for name, after in new.items():
            if name in old:
                continue
            self.schema_change(
                _Site(None, ParameterLocation('baseUri', name), 'request'),
                'added',
                'parameter',
                'request-property-added-required' if after.required else 'request-property-added',
                after=_parameter(after),
            )

    def api_protocols(self) -> None:
        old, new = _api_protocols(self.old_api), _api_protocols(self.new_api)
        if old != new:
            self.operation_change(
                None, TransportLocation(), 'changed', 'protocol', _protocol_rule(old, new), 'protocols', old, new
            )

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
        if old != new:
            self.operation_change(
                operation, TransportLocation(), 'changed', 'protocol', _protocol_rule(old, new), 'protocols', old, new
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
            _Site(operation, ParameterLocation('query', 'queryString'), 'request'),
            None if old is None else old.query_string,
            None if new is None else new.query_string,
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
                    'entity-removed',
                    before=_descriptor(before.description),
                )
                continue
            self.parameters(operation, before.headers, after.headers, 'header', response_status=status)
            self.bodies(operation, before.bodies, after.bodies, 'response', status=status)
            self.documentation(operation, ResponseStatus(status), before, after)
        for status in new:
            if status in old:
                continue
            self.operation_change(
                operation,
                ResponseStatus(status),
                'added',
                'response',
                'entity-added',
                after=_descriptor(new[status].description),
            )

    def documentation(
        self,
        operation: OperationId | None,
        location: OperationLocation,
        old: Operation | Response,
        new: Operation | Response,
    ) -> None:
        """`displayName` and `description`, wherever a node carries both.

        Two callers wrote the pair out in full -- nine lines each, identical but
        for the attribute name -- so the pair lives here and a caller names the
        node instead.
        """
        for attribute, before, after in (
            ('displayName', _facet(old.display_name), _facet(new.display_name)),
            ('description', _facet(old.description), _facet(new.description)),
        ):
            if before != after:
                self.operation_change(
                    operation, location, 'changed', 'documentation', 'documentation-changed', attribute, before, after
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
                self.operation_change(operation, location, 'removed', 'parameter', rule, before=_parameter(before))
                continue
            self.parameter_required(operation, location, before.required, after.required, direction)
            self.shape(_Site(operation, location, direction), before.base, after.base, {})
        for name, after in new.items():
            if name in old:
                continue
            if direction == 'request' and after.required:
                rule = 'request-property-added-required'
            else:
                rule = f'{direction}-property-added'
            self.operation_change(
                operation,
                ParameterLocation(binding, name, response_status),
                'added',
                'parameter',
                rule,
                after=_parameter(after),
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
        def at(media_type: str) -> RequestBody | ResponseBody:
            return RequestBody(media_type) if direction == 'request' else ResponseBody(status or '', media_type)

        for media_type, before in old.items():
            after = new.get(media_type)
            if after is None:
                self.operation_change(
                    operation,
                    at(media_type),
                    'removed',
                    'body',
                    'entity-removed',
                    before=_shape_described(before.shape),
                )
                continue
            self.optional_shape(_Site(operation, at(media_type), direction), before.shape, after.shape)
        for media_type in new:
            if media_type in old:
                continue
            self.operation_change(
                operation,
                at(media_type),
                'added',
                'body',
                'entity-added',
                after=_shape_described(new[media_type].shape),
            )

    def security(self, operation: OperationId | None, old: list[SecurityScheme], new: list[SecurityScheme]) -> None:
        location = SecurityLocation()
        old_open = not old or any(scheme.is_null for scheme in old)
        new_open = not new or any(scheme.is_null for scheme in new)
        if old_open != new_open:
            rule = 'security-added' if not new_open else 'security-removed'
            self.operation_change(
                operation, location, 'changed', 'security', rule, 'required', not old_open, not new_open
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
                    'security-alternative-removed',
                    'name',
                    before=_scheme(before),
                )
                continue
            self.scopes(operation, before, after)
            self.settings(operation, before, after)
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
                'security-alternative-added',
                'name',
                after=_scheme(arrival),
            )

    def scopes(self, operation: OperationId | None, before: SecurityScheme, after: SecurityScheme) -> None:
        """A narrowed OAuth scope list refuses a token the old one accepted."""
        old = tuple(sorted(before.compiled_params or ()))
        new = tuple(sorted(after.compiled_params or ()))
        if old == new:
            return
        rule = 'security-added' if set(new) - set(old) else 'security-removed'
        self.operation_change(operation, SecurityLocation(), 'changed', 'security', rule, 'scopes', old, new)

    def settings(self, operation: OperationId | None, before: SecurityScheme, after: SecurityScheme) -> None:
        """An endpoint a scheme points at, retargeted: the two may not agree."""
        old, new = _scheme_definition(before), _scheme_definition(after)
        for setting in sorted(old.keys() | new.keys()):
            old_value, new_value = old.get(setting), new.get(setting)
            if old_value != new_value:
                self.operation_change(
                    operation,
                    SecurityLocation(),
                    'changed',
                    'security-setting',
                    'reference-retargeted',
                    setting,
                    old_value,
                    new_value,
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
        if old != new:
            became = 'required' if new else 'optional'
            self.operation_change(
                operation, location, 'changed', 'required', f'{direction}-property-{became}', 'required', old, new
            )

    def operation_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        operation: OperationId | None,
        location: OperationLocation,
        kind: ChangeKind,
        subject: Subject,
        rule: str,
        attribute: str | None = None,
        before: object = None,
        after: object = None,
    ) -> None:
        """No owner means an API-level default, compared once at the root.

        The mirror of `schema_change` below. `OperationContract` is the one
        coordinate that cannot arrive here without one -- the API node's own
        facets are `ApiContract` -- so it is the one case this refuses.
        """
        if operation is None:
            if isinstance(location, OperationContract):
                raise AssertionError('the API root has no operation contract to change')
            self.changes.append(ApiChanged(location, kind, subject, attribute, before, after, impact_of(rule), rule))
        else:
            self.changes.append(
                OperationChanged(operation, location, kind, subject, attribute, before, after, impact_of(rule), rule)
            )

    def optional_shape(self, site: _Site, old: BaseShape | None, new: BaseShape | None) -> None:
        if old is None or new is None:
            if old is not new:
                kind: ChangeKind = 'added' if old is None else 'removed'
                self.schema_change(
                    site,
                    kind,
                    'type',
                    'type-changed',
                    before=None if old is None else old.type,
                    after=None if new is None else new.type,
                )
            return
        self.shape(site, old, new, {})

    def shape(self, site: _Site, old: BaseShape, new: BaseShape, ancestors: dict[BaseShape, BaseShape]) -> None:
        old_kind, new_kind = old.shape, new.shape
        if old_kind is None or new_kind is None or type(old_kind) is not type(new_kind):
            self.schema_change(site, 'changed', 'type', 'type-changed', 'type', old.type, new.type)
            return
        if isinstance(old_kind, RecursiveShape) or isinstance(new_kind, RecursiveShape):
            if not (
                isinstance(old_kind, RecursiveShape)
                and isinstance(new_kind, RecursiveShape)
                and (old_kind.head.name, old_kind.head.type) == (new_kind.head.name, new_kind.head.type)
            ):
                self.schema_change(
                    site, 'changed', 'type', 'other', 'recursionHead', _shape_name(old_kind), _shape_name(new_kind)
                )
            return
        if isinstance(old_kind, JsonShape) and isinstance(new_kind, JsonShape):
            self.schema_scalar(site, 'type', 'other', 'schema', old_kind.raw, new_kind.raw)
            return
        ancestors[old] = new
        try:
            self.shape_facets(site, old, new)
            if isinstance(old_kind, ObjectShape) and isinstance(new_kind, ObjectShape):
                self.object_shape(site, old_kind, new_kind, ancestors)
            elif isinstance(old_kind, ArrayShape) and isinstance(new_kind, ArrayShape):
                self.shape_pair(site.at(ItemsSegment()), old_kind.items, new_kind.items, ancestors)
            elif isinstance(old_kind, UnionShape) and isinstance(new_kind, UnionShape):
                self.union_shape(site, old_kind, new_kind, ancestors)
        finally:
            del ancestors[old]

    def shape_pair(
        self, site: _Site, old: BaseShape | None, new: BaseShape | None, ancestors: dict[BaseShape, BaseShape]
    ) -> None:
        """`shape` where both sides exist, `optional_shape` where one may not.

        The ancestor map is what separates them: it is the recursion guard, and it
        has nothing to guard where one side is absent.
        """
        if old is None or new is None:
            self.optional_shape(site, old, new)
        else:
            self.shape(site, old, new, ancestors)

    def shape_facets(self, site: _Site, old: BaseShape, new: BaseShape) -> None:
        old_facets = {name: _literal(facet.value) for name, facet in facets_of(old.shape)}
        new_facets = {name: _literal(facet.value) for name, facet in facets_of(new.shape)}
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            old_facets.pop('format', None)
            new_facets.pop('format', None)
        for name in sorted(old_facets.keys() | new_facets.keys()):
            before, after = old_facets.get(name), new_facets.get(name)
            if before != after:
                self.schema_change(
                    site, 'changed', 'constraint', _facet_rule(site.direction, name, before, after), name, before, after
                )
        self.enum(site, old, new)
        self.schema_documentation(site, old, new)
        self.schema_scalar(site, 'custom-facet', 'other', 'customFacets', _custom(old), _custom(new))
        if isinstance(old.shape, FileShape) and isinstance(new.shape, FileShape):
            before_types = _facet_list(old.shape.file_types)
            after_types = _facet_list(new.shape.file_types)
            self.schema_scalar(site, 'constraint', 'other', 'fileTypes', before_types, after_types)
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            before_format = _facet(old.shape.format) or 'rfc3339'
            after_format = _facet(new.shape.format) or 'rfc3339'
            self.schema_scalar(site, 'constraint', 'format-changed', 'format', before_format, after_format)

    def schema_documentation(self, site: _Site, old: BaseShape, new: BaseShape) -> None:
        """`documentation`'s mirror for a shape, which is located by a path."""
        self.schema_scalar(
            site,
            'documentation',
            'documentation-changed',
            'displayName',
            _facet(old.display_name),
            _facet(new.display_name),
        )
        self.schema_scalar(
            site,
            'documentation',
            'documentation-changed',
            'description',
            _facet(old.description),
            _facet(new.description),
        )

    def enum(self, site: _Site, old: BaseShape, new: BaseShape) -> None:
        before, after = _enum(old), _enum(new)
        if before == after:
            return
        # Neither row carries an `attribute`, for the same reason a property change
        # carries none: the members that left and arrived are the change, and `enum`
        # would be the container they left. `kind` says which side each row states,
        # so the renderer never reads an empty `after` as "the enum is gone".
        removed, added = _enum_delta(before, after)
        if removed:
            self.schema_change(site, 'removed', 'enum-value', f'{site.direction}-enum-value-removed', before=removed)
        if added:
            self.schema_change(site, 'added', 'enum-value', f'{site.direction}-enum-value-added', after=added)

    def object_shape(
        self, site: _Site, old: ObjectShape, new: ObjectShape, ancestors: dict[BaseShape, BaseShape]
    ) -> None:
        direction = site.direction
        old_properties, new_properties = old.properties or {}, new.properties or {}
        for name, before in old_properties.items():
            child = site.at(PropertySegment(name))
            after = new_properties.get(name)
            if after is None:
                rule = f'{direction}-property-removed'
                self.schema_change(child, 'removed', 'property', rule, before=_property(before))
                continue
            self.required(child, before.required, after.required)
            self.shape(child, before.base, after.base, ancestors)
        for name, after in new_properties.items():
            if name in old_properties:
                continue
            if direction == 'request' and after.required:
                rule = 'request-property-added-required'
            else:
                rule = f'{direction}-property-added'
            self.schema_change(site.at(PropertySegment(name)), 'added', 'property', rule, after=_property(after))
        self.pattern_properties(site, old, new, ancestors)

    def pattern_properties(
        self, site: _Site, old: ObjectShape, new: ObjectShape, ancestors: dict[BaseShape, BaseShape]
    ) -> None:
        """A `/regex/` key, whose first match wins -- so their order is a contract."""
        direction = site.direction
        old_patterns, new_patterns = old.pattern_properties or {}, new.pattern_properties or {}
        common = old_patterns.keys() & new_patterns.keys()
        old_order = tuple(name for name in old_patterns if name in common)
        new_order = tuple(name for name in new_patterns if name in common)
        if old_order != new_order:
            self.schema_change(site, 'changed', 'pattern-property', 'other', 'order', old_order, new_order)
        for pattern, before in old_patterns.items():
            child = site.at(PatternPropertySegment(pattern))
            after = new_patterns.get(pattern)
            if after is None:
                rule = f'{direction}-constraint-loosened'
                self.schema_change(child, 'removed', 'pattern-property', rule, 'pattern', before.base.type)
                continue
            self.shape(child, before.base, after.base, ancestors)
        for pattern, after in new_patterns.items():
            if pattern in old_patterns:
                continue
            self.schema_change(
                site.at(PatternPropertySegment(pattern)),
                'added',
                'pattern-property',
                f'{direction}-constraint-tightened',
                'pattern',
                after=after.base.type,
            )

    def union_shape(self, site: _Site, old: UnionShape, new: UnionShape, ancestors: dict[BaseShape, BaseShape]) -> None:
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
            child = site.at(UnionMemberSegment(before.name, before.type, old_occurrences[key]))
            if not candidates:
                rule = f'{site.direction}-enum-value-removed'
                self.schema_change(child, 'removed', 'union-member', rule, before=_shape_described(before))
                continue
            fingerprint = _shape_fingerprint(before)
            exact = next((candidate for candidate in candidates if new_fingerprints[candidate[0]] == fingerprint), None)
            if exact is None:
                new_index, after = candidates.popleft()
            else:
                candidates.remove(exact)
                new_index, after = exact
            matched.add(new_index)
            self.shape(child, before, after, ancestors)
        new_occurrences: dict[tuple[str, str], int] = defaultdict(int)
        for index, after in enumerate(new_members):
            key = _member_key(after)
            new_occurrences[key] += 1
            if index in matched:
                continue
            self.schema_change(
                site.at(UnionMemberSegment(after.name, after.type, new_occurrences[key])),
                'added',
                'union-member',
                f'{site.direction}-enum-value-added',
                after=_shape_described(after),
            )

    def required(
        self,
        site: _Site,
        old: bool,  # noqa: FBT001 - the old facet value
        new: bool,  # noqa: FBT001 - the new facet value
    ) -> None:
        if old != new:
            became = 'required' if new else 'optional'
            rule = f'{site.direction}-property-{became}'
            self.schema_change(site, 'changed', 'required', rule, 'required', old, new)

    def schema_scalar(  # noqa: PLR0913, PLR0917 - a scalar delta at one coordinate
        self,
        site: _Site,
        subject: Subject,
        rule: str,
        attribute: str,
        before: object,
        after: object,
    ) -> None:
        if before != after:
            self.schema_change(site, 'changed', subject, rule, attribute, before, after)

    def schema_change(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        site: _Site,
        kind: ChangeKind,
        subject: Subject,
        rule: str,
        attribute: str | None = None,
        before: object = None,
        after: object = None,
    ) -> None:
        """No owner means an API-level default, compared once at the root.

        The mirror of `operation_change` above, and the one place the walk reads
        `site.operation`. Narrowed by check rather than by `cast`: `baseUriParameters`
        is the only API-scoped shape RAML has, so a second no-owner caller over a
        body would otherwise file a body change under `ParameterLocation` and say
        nothing about it.
        """
        impact = impact_of(rule)
        if site.operation is None:
            if not isinstance(site.location, ParameterLocation):
                raise AssertionError(f'an API-scoped shape change needs a parameter location, not {site.location!r}')
            self.changes.append(
                ApiSchemaChanged(site.location, site.path, kind, subject, attribute, before, after, impact, rule)
            )
        else:
            self.changes.append(
                SchemaChanged(
                    site.operation, site.location, site.path, kind, subject, attribute, before, after, impact, rule
                )
            )


def _operations(raml: Raml) -> dict[OperationId, tuple[EndPoint, Operation]]:
    return {
        OperationId(endpoint.full_uri, method): (endpoint, operation)
        for endpoint in raml.endpoints.values()
        for method, operation in endpoint.operations.items()
    }


def _protocol_rule(old: tuple[str, ...], new: tuple[str, ...]) -> str:
    return 'protocol-removed' if any(item not in new for item in old) else 'protocol-added'


def _api_protocols(api: APIFragment | None) -> tuple[str, ...]:
    return () if api is None else tuple(facet.value for facet in api.protocols)


def _facet_rule(direction: Direction, attribute: str, before: object, after: object) -> str:
    if attribute == 'format' and (before in DATETIME_FORMATS or after in DATETIME_FORMATS):
        return 'format-changed'
    loosened = _loosened(attribute, before, after)
    if loosened is None:
        return 'other'
    return f'{direction}-constraint-{"loosened" if loosened else "tightened"}'


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
    if attribute not in _UPPER and attribute not in _LOWER:
        # Nothing here can order it, so say so and let the rule be `other`. The
        # comparison below is meaningful only for the eight facets these two sets
        # name; falling through to it grades anything that is not an upper bound
        # as though it were a lower one.
        return None
    old_number, new_number = _numeric(before), _numeric(after)
    if old_number is None and new_number is None:
        return None
    # A bound that appeared tightens and one that vanished loosens, whichever
    # end it is: neither branch needs to know which set the attribute is in.
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


def _facet(value: object | None) -> object | None:
    return getattr(value, 'value', None)


def _text_facet(value: object | None) -> str | None:
    raw = _facet(value)
    return raw if isinstance(raw, str) else None


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
