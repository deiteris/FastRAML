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
from typing import TYPE_CHECKING, Final, Literal, Protocol

from fastraml.parser.fragments import APIFragment
from fastraml.types.base import BaseShape, Parameter, facets_of
from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import DATETIME_FORMATS, INTEGER_FORMATS, DateTimeShape, FileShape
from fastraml.types.values import decimal_text
from fastraml.views.backward.model import (
    Change,
    Changed,
    ChangeKind,
    Direction,
    ItemsSegment,
    Location,
    OperationAdded,
    OperationContract,
    OperationId,
    OperationRemoved,
    ParameterLocation,
    PathSegment,
    PatternPropertySegment,
    PropertySegment,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SecurityLocation,
    Subject,
    TransportLocation,
    TypeDeclaration,
    UnionMemberSegment,
    impact_of,
    rule_for,
    side_of,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    class _Described(Protocol):
        """Anything carrying the two prose facets: an operation, a response, a shape.

        Read-only members, so the protocol is covariant in them and a concrete
        `ScalarFacet[str] | None` satisfies it. Nothing writes through this.
        """

        @property
        def display_name(self) -> object: ...

        @property
        def description(self) -> object: ...

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


def backward_types(old: Raml, new: Raml) -> list[Change]:
    """Compare the `types:` declarations of two models parsed with ``unwrap=True``.

    For a library, or for an API whose types are the contract other documents
    build on. A declaration is on neither side of the wire, so every change is
    graded twice -- once for anyone sending a value of that type and once for
    anyone reading one -- and both grades are in the list. `side_of_rule` says
    which half a record is, and the report pairs them into one row.
    """
    analyzer = _Backward(old, new)
    analyzer.declarations(_declared(old), _declared(new))
    return analyzer.changes


def _declared(raml: Raml) -> dict[str, BaseShape]:
    """`types:` off the entry point, whether that is an API or a Library."""
    return dict(getattr(raml.entry_point, 'types', None) or {})


_NUMBER_WIDTH: Final = {'float': 0, 'double': 1}


@dataclass(frozen=True, slots=True)
class _At:
    """Where the walk is: an owner, a coordinate, and a path if it is in a shape.

    `path is None` is a contract change -- the coordinate in `location` is the
    thing that moved -- and `path == ()` is the root of a shape hanging off it.
    `operation is None` is the API scope, which `emit` is the only reader of.

    There is no `direction` field. It is `side_of(location)`, it was measured to
    agree with the threaded value at every at in the corpus, and a stored copy
    of a neighbour's function is how the two eventually stop agreeing.
    """

    location: Location
    operation: OperationId | None = None
    path: tuple[PathSegment, ...] | None = None

    def at(self, segment: PathSegment) -> _At:
        """The same coordinate, one step further into the shape."""
        return _At(self.location, self.operation, (*(self.path or ()), segment))

    def shape(self) -> _At:
        """The same coordinate, at the root of the shape hanging off it."""
        return _At(self.location, self.operation, ())


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
        self.documentation(_At(OperationContract(), operation_id), old, new)

    def base_uri(self) -> None:
        old = _facet(self.old_api.base_uri) if self.old_api is not None else None
        new = _facet(self.new_api.base_uri) if self.new_api is not None else None
        if old != new:
            self.emit(_At(TransportLocation(), None), 'changed', 'base-uri', 'base-uri-changed', 'baseUri', old, new)

    def api_parameters(self) -> None:
        old = {} if self.old_api is None else self.old_api.base_uri_parameters
        new = {} if self.new_api is None else self.new_api.base_uri_parameters
        for name, before in old.items():
            after = new.get(name)
            at = _At(ParameterLocation('baseUri', name), path=())
            if after is None:
                self.emit(at, 'removed', 'parameter', 'property-removed', before=_parameter(before))
                continue
            # The same requiredness rule a property gets, and the same walk over the
            # shape: an API-scoped parameter differs from an operation's in who owns
            # it, which is the one thing `_At` carries and nothing here re-decides.
            self.required(at, before.required, after.required)
            self.shape(at, before.base, after.base, {})
        for name, after in new.items():
            if name in old:
                continue
            self.emit(
                _At(ParameterLocation('baseUri', name), path=()),
                'added',
                'parameter',
                'property-added-required' if after.required else 'property-added',
                after=_parameter(after),
            )

    def api_protocols(self) -> None:
        old, new = _api_protocols(self.old_api), _api_protocols(self.new_api)
        if old != new:
            self.emit(
                _At(TransportLocation(), None), 'changed', 'protocol', _protocol_rule(old, new), 'protocols', old, new
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
        the root is one change that reaches every method, reported once by
        `api_protocols` rather than once per method. An operation that declares
        its own protocols makes its own statement and is compared here.
        """
        old_declared, new_declared = tuple(old_operation.protocols), tuple(new_operation.protocols)
        if not old_declared and not new_declared:
            return
        old = old_declared or _api_protocols(self.old_api)
        new = new_declared or _api_protocols(self.new_api)
        if old != new:
            self.emit(
                _At(TransportLocation(), operation),
                'changed',
                'protocol',
                _protocol_rule(old, new),
                'protocols',
                old,
                new,
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
            _At(ParameterLocation('query', 'queryString'), operation, ()),
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
                self.emit(
                    _At(ResponseStatus(status), operation),
                    'removed',
                    'response',
                    'entity-removed',
                    before=_descriptor(before.description),
                )
                continue
            self.parameters(operation, before.headers, after.headers, 'header', response_status=status)
            self.bodies(operation, before.bodies, after.bodies, 'response', status=status)
            self.documentation(_At(ResponseStatus(status), operation), before, after)
        for status in new:
            if status in old:
                continue
            self.emit(
                _At(ResponseStatus(status), operation),
                'added',
                'response',
                'entity-added',
                after=_descriptor(new[status].description),
            )

    def documentation(self, at: _At, old: _Described, new: _Described) -> None:
        """`displayName` and `description`, wherever anything carries both.

        An operation, a response and a shape all do.
        """
        for attribute, before, after in (
            ('displayName', _facet(old.display_name), _facet(new.display_name)),
            ('description', _facet(old.description), _facet(new.description)),
        ):
            self.scalar(at, 'documentation', 'documentation-changed', attribute, before, after)

    def parameters(
        self,
        operation: OperationId | None,
        old: Mapping[str, Parameter],
        new: Mapping[str, Parameter],
        binding: Literal['path', 'query', 'header', 'baseUri'],
        *,
        response_status: str | None = None,
    ) -> None:
        for name, before in old.items():
            after = new.get(name)
            location = ParameterLocation(binding, name, response_status)
            if after is None:
                rule = 'property-removed'
                self.emit(_At(location, operation), 'removed', 'parameter', rule, before=_parameter(before))
                continue
            self.required(_At(location, operation), before.required, after.required)
            self.shape(_At(location, operation, ()), before.base, after.base, {})
        for name, after in new.items():
            if name in old:
                continue
            rule = 'property-added-required' if after.required else 'property-added'
            self.emit(
                _At(ParameterLocation(binding, name, response_status), operation),
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
                self.emit(
                    _At(at(media_type), operation),
                    'removed',
                    'body',
                    'entity-removed',
                    before=_shape_described(before.shape),
                )
                continue
            self.optional_shape(_At(at(media_type), operation, ()), before.shape, after.shape)
        for media_type in new:
            if media_type in old:
                continue
            self.emit(
                _At(at(media_type), operation),
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
            self.emit(_At(location, operation), 'changed', 'security', rule, 'required', not old_open, not new_open)
        if old_open or new_open:
            return
        old_by_name = {scheme.name: scheme for scheme in old}
        new_by_name = {scheme.name: scheme for scheme in new}
        for name, before in old_by_name.items():
            after = new_by_name.get(name)
            if after is None:
                self.emit(
                    _At(location, operation),
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
            self.emit(
                _At(location, operation),
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
        self.emit(_At(SecurityLocation(), operation), 'changed', 'security', rule, 'scopes', old, new)

    def settings(self, operation: OperationId | None, before: SecurityScheme, after: SecurityScheme) -> None:
        """An endpoint a scheme points at, retargeted: the two may not agree."""
        old, new = _scheme_definition(before), _scheme_definition(after)
        for setting in sorted(old.keys() | new.keys()):
            old_value, new_value = old.get(setting), new.get(setting)
            if old_value != new_value:
                self.emit(
                    _At(SecurityLocation(), operation),
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

    def optional_shape(self, at: _At, old: BaseShape | None, new: BaseShape | None) -> None:
        if old is None or new is None:
            if old is not new:
                kind: ChangeKind = 'added' if old is None else 'removed'
                self.emit(
                    at,
                    kind,
                    'type',
                    'type-changed',
                    before=None if old is None else old.type,
                    after=None if new is None else new.type,
                )
            return
        self.shape(at, old, new, {})

    def shape(self, at: _At, old: BaseShape, new: BaseShape, ancestors: dict[BaseShape, BaseShape]) -> None:
        old_kind, new_kind = old.shape, new.shape
        if old_kind is None or new_kind is None or type(old_kind) is not type(new_kind):
            self.emit(at, 'changed', 'type', 'type-changed', 'type', old.type, new.type)
            return
        if isinstance(old_kind, RecursiveShape) or isinstance(new_kind, RecursiveShape):
            if not (
                isinstance(old_kind, RecursiveShape)
                and isinstance(new_kind, RecursiveShape)
                and (old_kind.head.name, old_kind.head.type) == (new_kind.head.name, new_kind.head.type)
            ):
                self.emit(at, 'changed', 'type', 'other', 'recursionHead', _shape_name(old_kind), _shape_name(new_kind))
            return
        if isinstance(old_kind, JsonShape) and isinstance(new_kind, JsonShape):
            self.scalar(at, 'type', 'other', 'schema', old_kind.raw, new_kind.raw)
            return
        ancestors[old] = new
        try:
            self.shape_facets(at, old, new)
            if isinstance(old_kind, ObjectShape) and isinstance(new_kind, ObjectShape):
                self.object_shape(at, old_kind, new_kind, ancestors)
            elif isinstance(old_kind, ArrayShape) and isinstance(new_kind, ArrayShape):
                self.shape_pair(at.at(ItemsSegment()), old_kind.items, new_kind.items, ancestors)
            elif isinstance(old_kind, UnionShape) and isinstance(new_kind, UnionShape):
                self.union_shape(at, old_kind, new_kind, ancestors)
        finally:
            del ancestors[old]

    def shape_pair(
        self, at: _At, old: BaseShape | None, new: BaseShape | None, ancestors: dict[BaseShape, BaseShape]
    ) -> None:
        """`shape` where both sides exist, `optional_shape` where one may not.

        The ancestor map is what separates them: it is the recursion guard, and it
        has nothing to guard where one side is absent.
        """
        if old is None or new is None:
            self.optional_shape(at, old, new)
        else:
            self.shape(at, old, new, ancestors)

    def shape_facets(self, at: _At, old: BaseShape, new: BaseShape) -> None:
        old_facets = {name: _literal(facet.value) for name, facet in facets_of(old.shape)}
        new_facets = {name: _literal(facet.value) for name, facet in facets_of(new.shape)}
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            old_facets.pop('format', None)
            new_facets.pop('format', None)
        for name in sorted(old_facets.keys() | new_facets.keys()):
            before, after = old_facets.get(name), new_facets.get(name)
            if before != after:
                self.emit(at, 'changed', 'constraint', _facet_movement(name, before, after), name, before, after)
        self.enum(at, old, new)
        self.documentation(at, old, new)
        self.scalar(at, 'custom-facet', 'other', 'customFacets', _custom(old), _custom(new))
        if isinstance(old.shape, FileShape) and isinstance(new.shape, FileShape):
            before_types = _facet_list(old.shape.file_types)
            after_types = _facet_list(new.shape.file_types)
            self.scalar(at, 'constraint', 'other', 'fileTypes', before_types, after_types)
        if isinstance(old.shape, DateTimeShape) and isinstance(new.shape, DateTimeShape):
            before_format = _facet(old.shape.format) or 'rfc3339'
            after_format = _facet(new.shape.format) or 'rfc3339'
            self.scalar(at, 'constraint', 'format-changed', 'format', before_format, after_format)

    def enum(self, at: _At, old: BaseShape, new: BaseShape) -> None:
        before, after = _enum(old), _enum(new)
        if before == after:
            return
        # Neither row carries an `attribute`, for the same reason a property change
        # carries none: the members that left and arrived are the change, and `enum`
        # would be the container they left. `kind` says which side each row states,
        # so the renderer never reads an empty `after` as "the enum is gone".
        removed, added = _enum_delta(before, after)
        if removed:
            self.emit(at, 'removed', 'enum-value', 'enum-value-removed', before=removed)
        if added:
            self.emit(at, 'added', 'enum-value', 'enum-value-added', after=added)

    def object_shape(self, at: _At, old: ObjectShape, new: ObjectShape, ancestors: dict[BaseShape, BaseShape]) -> None:
        old_properties, new_properties = old.properties or {}, new.properties or {}
        for name, before in old_properties.items():
            child = at.at(PropertySegment(name))
            after = new_properties.get(name)
            if after is None:
                rule = 'property-removed'
                self.emit(child, 'removed', 'property', rule, before=_property(before))
                continue
            self.required(child, before.required, after.required)
            self.shape(child, before.base, after.base, ancestors)
        for name, after in new_properties.items():
            if name in old_properties:
                continue
            rule = 'property-added-required' if after.required else 'property-added'
            self.emit(at.at(PropertySegment(name)), 'added', 'property', rule, after=_property(after))
        self.pattern_properties(at, old, new, ancestors)

    def pattern_properties(
        self, at: _At, old: ObjectShape, new: ObjectShape, ancestors: dict[BaseShape, BaseShape]
    ) -> None:
        """A `/regex/` key, whose first match wins -- so their order is a contract."""
        old_patterns, new_patterns = old.pattern_properties or {}, new.pattern_properties or {}
        common = old_patterns.keys() & new_patterns.keys()
        old_order = tuple(name for name in old_patterns if name in common)
        new_order = tuple(name for name in new_patterns if name in common)
        if old_order != new_order:
            self.emit(at, 'changed', 'pattern-property', 'other', 'order', old_order, new_order)
        for pattern, before in old_patterns.items():
            child = at.at(PatternPropertySegment(pattern))
            after = new_patterns.get(pattern)
            if after is None:
                rule = 'constraint-loosened'
                self.emit(child, 'removed', 'pattern-property', rule, 'pattern', before.base.type)
                continue
            self.shape(child, before.base, after.base, ancestors)
        for pattern, after in new_patterns.items():
            if pattern in old_patterns:
                continue
            self.emit(
                at.at(PatternPropertySegment(pattern)),
                'added',
                'pattern-property',
                'constraint-tightened',
                'pattern',
                after=after.base.type,
            )

    def union_shape(self, at: _At, old: UnionShape, new: UnionShape, ancestors: dict[BaseShape, BaseShape]) -> None:
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
            child = at.at(UnionMemberSegment(before.name, before.type, old_occurrences[key]))
            if not candidates:
                rule = 'enum-value-removed'
                self.emit(child, 'removed', 'union-member', rule, before=_shape_described(before))
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
            self.emit(
                at.at(UnionMemberSegment(after.name, after.type, new_occurrences[key])),
                'added',
                'union-member',
                'enum-value-added',
                after=_shape_described(after),
            )

    def required(
        self,
        at: _At,
        old: bool,  # noqa: FBT001 - the old facet value
        new: bool,  # noqa: FBT001 - the new facet value
    ) -> None:
        """One rule for one question, wherever the thing that can be required is.

        A query parameter and a property are the same statement about the same
        wire, so they get the same rule. This had two implementations differing
        only in which emitter each called, and the parameter one took a
        `direction` its coordinate already knew.
        """
        if old != new:
            became = 'required' if new else 'optional'
            rule = f'property-{became}'
            self.emit(at, 'changed', 'required', rule, 'required', old, new)

    def scalar(  # noqa: PLR0913, PLR0917 - a scalar delta at one coordinate
        self,
        at: _At,
        subject: Subject,
        rule: str,
        attribute: str,
        before: object,
        after: object,
    ) -> None:
        if before != after:
            self.emit(at, 'changed', subject, rule, attribute, before, after)

    def emit(  # noqa: PLR0913, PLR0917 - mirrors the immutable result
        self,
        at: _At,
        kind: ChangeKind,
        subject: Subject,
        movement: str,
        attribute: str | None = None,
        before: object = None,
        after: object = None,
    ) -> None:
        """The one place a change is built, and the one place a side is decided.

        `movement` names what happened -- `property-removed`,
        `constraint-tightened` -- and `rule_for` turns it into a rule id using
        the side the coordinate sits on. The walk therefore never says `request`
        or `response`, which is what lets one traversal answer a coordinate that
        has no side.

        **A coordinate with no side gets both.** A declared type is neither sent
        nor received until something uses it, so removing a property from one is
        `review` for anyone sending it and `breaking` for anyone reading it, and
        both are true. That is two changes rather than one with two grades: each
        carries the single `impact` that `configure` overrides, `--severity`
        filters and the exit code reads, and the report pairs them into one row.
        A movement with no side of its own -- `type-changed` -- still yields one.

        The API root has no method contract, so `OperationContract` cannot arrive
        without an owner. Refused here rather than in the types: it is a fact
        about the walk, and the alternative is a silent change filed nowhere.
        """
        if at.operation is None and isinstance(at.location, OperationContract):
            raise AssertionError('the API root has no operation contract to change')
        side = side_of(at.location)
        sides: tuple[Direction | None, ...] = (side,) if side is not None else ('request', 'response')
        emitted: list[str] = []
        for direction in sides:
            rule = rule_for(movement, direction)
            if rule in emitted:
                continue
            emitted.append(rule)
            self.changes.append(
                Changed(
                    at.operation,
                    at.location,
                    at.path,
                    kind,
                    subject,
                    attribute,
                    before,
                    after,
                    impact_of(rule),
                    rule,
                )
            )

    def declarations(self, old: Mapping[str, BaseShape], new: Mapping[str, BaseShape]) -> None:
        """`types:` compared as declarations, for a library or a types-only pass.

        The same shape walk the endpoints use, at a coordinate that has no side,
        so every rule it reaches is graded both ways by `emit`. Nothing here is
        a second traversal: direction was never an input to the walk, only to
        the grading it fed.
        """
        for name, before in old.items():
            at = _At(TypeDeclaration(name), path=())
            after = new.get(name)
            if after is None:
                self.emit(
                    _At(TypeDeclaration(name)), 'removed', 'type', 'entity-removed', before=_shape_described(before)
                )
                continue
            self.shape(at, before, after, {})
        for name, after in new.items():
            if name not in old:
                self.emit(_At(TypeDeclaration(name)), 'added', 'type', 'entity-added', after=_shape_described(after))


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


def _facet_movement(attribute: str, before: object, after: object) -> str:
    if attribute == 'format' and (before in DATETIME_FORMATS or after in DATETIME_FORMATS):
        return 'format-changed'
    loosened = _loosened(attribute, before, after)
    if loosened is None:
        return 'other'
    return f'constraint-{"loosened" if loosened else "tightened"}'


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
        return decimal_text(value)
    pattern = getattr(value, 'pattern', None)
    return pattern if isinstance(pattern, str) else value


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
