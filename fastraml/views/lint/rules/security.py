"""OWASP-derived rules translated onto RAML's effective model."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from fastraml.parser.fragments import APIFragment
from fastraml.parser.security import TYPE_BASIC
from fastraml.parser.uritemplates import extract_uri_template_params
from fastraml.types.complex_ import ArrayShape, ObjectShape
from fastraml.types.scalars import AnyShape, DateTimeShape, IntegerShape, NumberShape, StringShape
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import EndPoint, Operation, Response
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.types.base import BaseShape
    from fastraml.views.lint.engine import Context

__all__ = [
    'BoundedAdditionalProperties',
    'BoundedArray',
    'BoundedInteger',
    'HttpsOnly',
    'InsecureBasicAuthentication',
    'IntegerFormat',
    'NoAdditionalProperties',
    'NumericResourceId',
    'RateLimitHeaders',
    'Required401Response',
    'Required429Response',
    'Required500Response',
    'RestrictedString',
    'RetryAfter429',
    'UnboundedString',
    'ValidationErrorResponse',
]

_RATE_HEADERS = frozenset(
    {
        'ratelimit',
        'ratelimit-policy',
        'x-ratelimit-limit',
        'x-ratelimit-remaining',
        'x-ratelimit-reset',
    }
)
_SUCCESS_CODES = range(200, 300)
_TOO_MANY_REQUESTS = 429


def _rate_header_usable(name: str, shape: object) -> bool:
    name = name.casefold()
    if name in {'ratelimit', 'ratelimit-policy'}:
        return isinstance(shape, StringShape)
    return isinstance(shape, IntegerShape)


def _effective_protocols(ctx: Context, operation: Operation) -> set[str]:
    values = operation.protocols or ctx.raml.global_protocols
    if values:
        return {value.casefold() for value in values}
    entry = ctx.raml.entry_point
    if isinstance(entry, APIFragment) and entry.base_uri is not None:
        return {entry.base_uri.value.partition(':')[0].casefold()}
    return set()


def _status_code(response: Response) -> int:
    """The decoder has already established a concrete 100-599 code."""
    return int(response.code)


def _has_typed_body(response: Response | None) -> bool:
    return response is not None and any(
        body.shape is not None and not isinstance(body.shape.shape, AnyShape) for body in response.bodies.values()
    )


def _request_type(declaration: str) -> str:
    return (
        f'#%RAML 1.0\ntitle: t\ntypes:\n  Input:\n{declaration}/a:\n  post:\n    body:\n      application/json: Input\n'
    )


def _is_input(ctx: Context, iri: str, base: BaseShape) -> bool:
    return base.alias is None and iri in ctx.graph.request_shape_iris()


class InsecureBasicAuthentication:
    meta: ClassVar = RuleMeta(
        'insecure-basic-authentication',
        Category.SECURITY,
        'Basic Authentication is declared',
        (
            'OWASP API2:2023 Broken Authentication. Basic sends the long-lived password itself, base64-encoded '
            'rather than encrypted, on every request, so each request exposes a reusable credential. Prefer a '
            'token-based scheme such as OAuth 2.0.'
        ),
        Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://example.com/token\n      authorizationGrants: [client_credentials]\n'
        ),
        bad='#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n',
    )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        if definition.resolved().type != TYPE_BASIC:
            return ()
        return (
            ctx.on(self.meta, 'security scheme uses Basic Authentication', definition, iri=iri, scheme=definition.name),
        )


class HttpsOnly:
    meta: ClassVar = RuleMeta(
        'https-only',
        Category.SECURITY,
        'operations should be HTTPS-only',
        (
            'OWASP API8:2023 Security Misconfiguration. Missing TLS is a listed misconfiguration, and OWASP asks '
            'that all API communication use an encrypted channel, whether the API is internal or public.'
        ),
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\nprotocols: [HTTPS]\n/a:\n  get:\n',
        bad='#%RAML 1.0\ntitle: t\nprotocols: [HTTP]\n/a:\n  get:\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        protocols = _effective_protocols(ctx, operation)
        if protocols == {'https'}:
            return ()
        return (
            ctx.on(
                self.meta,
                'operation is not restricted to HTTPS',
                operation,
                iri=iri,
                method=operation.method,
                protocols=','.join(sorted(protocols)) or 'unspecified',
            ),
        )


class _RequiredResponse:
    status: ClassVar[str]
    meta: ClassVar[RuleMeta]

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        response = operation.responses.get(self.status)
        if _has_typed_body(response):
            return ()
        return (
            ctx.on(
                self.meta,
                'operation lacks required response body',
                operation,
                iri=iri,
                method=operation.method,
                status=self.status,
            ),
        )


class Required401Response(_RequiredResponse):
    status = '401'
    meta = RuleMeta(
        'required-401-response',
        Category.SECURITY,
        'operations should document a 401 body',
        (
            'OWASP API8:2023 Security Misconfiguration. OWASP asks that every response payload schema be defined '
            'and enforced, including error responses, so that exception traces and other internal details are '
            'not sent back to attackers. A 401 is the error every authenticated operation can return.'
        ),
        Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n'
            '/a:\n  get:\n    securedBy: [basic]\n    responses:\n      401:\n'
            '        body:\n          application/json: string\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n'
            '/a:\n  get:\n    securedBy: [basic]\n'
        ),
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        if not operation.secured_by or any(scheme.is_null for scheme in operation.secured_by):
            return ()
        return super().operation(ctx, iri, operation)


class Required429Response(_RequiredResponse):
    status = '429'
    meta = RuleMeta(
        'required-429-response',
        Category.SECURITY,
        'operations should document a 429 body',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. OWASP asks for a limit on how often a client can '
            'call the API. A typed 429 documents that the limit exists and lets clients recognise it and back '
            'off, instead of retrying at once.'
        ),
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      429:\n        body:\n          application/json: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n',
    )


class Required500Response(_RequiredResponse):
    status = '500'
    meta = RuleMeta(
        'required-500-response',
        Category.SECURITY,
        'operations should document a 500 body',
        (
            'OWASP API8:2023 Security Misconfiguration. Error messages that include stack traces are a listed '
            'misconfiguration. OWASP asks that error response schemas be defined and enforced. An unexpected '
            'server failure is where an undefined 500 most often leaks one.'
        ),
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      500:\n        body:\n          application/json: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n',
    )


class ValidationErrorResponse:
    meta: ClassVar = RuleMeta(
        'validation-error-response',
        Category.SECURITY,
        'operations should document a 400 or 422 body',
        (
            'OWASP API8:2023 Security Misconfiguration. OWASP asks that error response schemas be defined and '
            'enforced. An operation that accepts input can reject it, and a typed 400 or 422 says what the '
            'rejection exposes instead of leaving it to the framework default.'
        ),
        Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    queryParameters:\n      q: string\n'
            '    responses:\n      400:\n        body:\n          application/json: string\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    queryParameters:\n      q: string\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        request = operation.request
        direct_input = request is not None and bool(
            request.headers or request.query_parameters or request.query_string is not None or request.bodies
        )
        endpoint_input = any(
            (endpoint := ctx.graph.endpoint_at(edge.subject)) is not None and endpoint.uri_parameters
            for edge in ctx.graph.into(iri, ('supportedOperation',))
        )
        if not direct_input and not endpoint_input:
            return ()
        if any(
            _has_typed_body(response) for response in (operation.responses.get('400'), operation.responses.get('422'))
        ):
            return ()
        return (
            ctx.on(
                self.meta,
                'operation lacks a validation error response body',
                operation,
                iri=iri,
                method=operation.method,
            ),
        )


class RateLimitHeaders:
    meta: ClassVar = RuleMeta(
        'rate-limit-headers',
        Category.SECURITY,
        'success and rate-limit responses should describe rate limits',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. OWASP asks for a limit on how often a client can '
            'call the API. Rate-limit headers document that limit and let well-behaved clients throttle '
            'themselves before they reach it.'
        ),
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        headers:\n          X-RateLimit-Limit: integer\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200: {}\n',
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        status = _status_code(response)
        relevant = status in _SUCCESS_CODES or status == _TOO_MANY_REQUESTS
        headers = [
            (name, parameter) for name, parameter in response.headers.items() if name.casefold() in _RATE_HEADERS
        ]
        if not relevant or any(_rate_header_usable(name, parameter.base.shape) for name, parameter in headers):
            return ()
        message = 'response has no rate-limit header' if not headers else 'response has no usable rate-limit header'
        return (ctx.on(self.meta, message, response, iri=iri, status=response.code),)


class RetryAfter429:
    meta: ClassVar = RuleMeta(
        'retry-after-429',
        Category.SECURITY,
        '429 responses should declare Retry-After',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. Retry-After tells a rate-limited client when to '
            'retry, so clients do not poll immediately and add to the load that caused the limit.'
        ),
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      429:\n        headers:\n          Retry-After: integer\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      429: {}\n',
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        if _status_code(response) != _TOO_MANY_REQUESTS:
            return ()
        header = next((value for name, value in response.headers.items() if name.casefold() == 'retry-after'), None)
        shape = None if header is None else header.base.shape
        http_date = isinstance(shape, DateTimeShape) and shape.format is not None and shape.format.value == 'rfc2616'
        if isinstance(shape, IntegerShape) or http_date:
            return ()
        message = '429 response has no Retry-After header' if header is None else 'Retry-After has an unusable type'
        return (
            ctx.on(
                self.meta,
                message,
                response,
                iri=iri,
                status=response.code,
                type='missing' if header is None else header.base.type,
            ),
        )


class NumericResourceId:
    meta: ClassVar = RuleMeta(
        'numeric-resource-id',
        Category.SECURITY,
        'URI template parameters should not be numeric',
        (
            'OWASP API1:2023 Broken Object Level Authorization. Sequential numeric IDs let an attacker enumerate '
            "other users' records, so OWASP prefers random, unpredictable values. This is an extra layer of "
            'defence: the fix OWASP requires is an authorization check on every record access.'
        ),
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/users/{userId}:\n  uriParameters:\n    userId: string\n',
        bad='#%RAML 1.0\ntitle: t\n/users/{userId}:\n  uriParameters:\n    userId: integer\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        names = (
            expression.name
            for expression in extract_uri_template_params(endpoint.uri, endpoint.location, endpoint.key_pos)
        )
        found = []
        for name in dict.fromkeys(names):
            parameter = endpoint.uri_parameters.get(name)
            if parameter is None or not isinstance(parameter.base.shape, (IntegerShape, NumberShape)):
                continue
            found.append(
                ctx.on(
                    self.meta,
                    'URI template parameter uses a numeric type',
                    parameter.base,
                    iri=iri,
                    position=parameter.key_pos,
                    parameter=name,
                    type=parameter.base.type,
                )
            )
        return found


class BoundedArray:
    meta: ClassVar = RuleMeta(
        'bounded-array',
        Category.SECURITY,
        'arrays should have maxItems',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. OWASP asks for a maximum number of elements in '
            'every incoming array. `maxItems` limits the memory and processing an attacker-supplied array can '
            'demand. Constrain the `items` type as well.'
        ),
        Severity.WARNING,
        good=_request_type('    type: array\n    items: string\n    maxItems: 10\n'),
        bad=_request_type('    type: array\n    items: string\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if not isinstance(base.shape, ArrayShape) or base.shape.max_items is not None or not _is_input(ctx, iri, base):
            return ()
        return (ctx.on(self.meta, 'array has no maximum item count', base, iri=iri, type=base.name or 'anonymous'),)


class RestrictedString:
    meta: ClassVar = RuleMeta(
        'restricted-string',
        Category.SECURITY,
        'strings should constrain accepted values',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. OWASP asks for server-side validation of query '
            'and body parameters. A `pattern` or `enum` states which values are accepted, so arbitrary text is '
            'not accepted by default.'
        ),
        Severity.WARNING,
        good=_request_type('    type: string\n    pattern: ^x+$\n'),
        bad=_request_type('    type: string\n    maxLength: 20\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if (
            not isinstance(base.shape, StringShape)
            or base.shape.pattern is not None
            or base.enum is not None
            or not _is_input(ctx, iri, base)
        ):
            return ()
        return (ctx.on(self.meta, 'string has no pattern or enum', base, iri=iri, type=base.name or 'anonymous'),)


class UnboundedString:
    """A string shape with no upper bound or restricted value domain."""

    meta: ClassVar = RuleMeta(
        id='unbounded-string',
        category=Category.SECURITY,
        summary='a string with no maxLength, pattern or enum',
        rationale=(
            'OWASP API4:2023 Unrestricted Resource Consumption. OWASP asks for a maximum length on every incoming '
            'string. A string with no size or value restriction allows unlimited memory use wherever the shape '
            'is used as input. Checking the shape rather than only its current use sites also '
            'covers named types before they are wired into an endpoint. `maxLength` supplies a direct bound; '
            '`pattern` and `enum` record an intentional accepted domain.'
        ),
        severity=Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\ntypes:\n  Input:\n    type: string\n    maxLength: 64\n'
            '/a:\n  post:\n    body:\n      text/plain: Input\n'
        ),
        bad=('#%RAML 1.0\ntitle: t\ntypes:\n  Input: string\n/a:\n  post:\n    body:\n      text/plain: Input\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if (
            not isinstance(shape, StringShape)
            or shape.max_length is not None
            or shape.pattern is not None
            or base.enum is not None
            or not _is_input(ctx, iri, base)
        ):
            return ()
        name = base.name
        if not name:
            parent = next(iter(ctx.graph.into(iri, ('anyOf',))), None)
            if parent is not None:
                name = ctx.graph.nodes[parent.subject].name
        return (ctx.on(self.meta, 'string type is unbounded', base, iri=iri, type=name or 'anonymous'),)


class IntegerFormat:
    meta: ClassVar = RuleMeta(
        'integer-format',
        Category.SECURITY,
        'integers should declare a storage width',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. An explicit `format` fixes the range a server '
            'must accept. Without it, generators and validators each choose a width, and an out-of-range value '
            'can overflow or be accepted by one and rejected by another.'
        ),
        Severity.WARNING,
        good=_request_type('    type: integer\n    format: int32\n'),
        bad=_request_type('    type: integer\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if not isinstance(base.shape, IntegerShape) or base.shape.format is not None or not _is_input(ctx, iri, base):
            return ()
        return (ctx.on(self.meta, 'integer has no format', base, iri=iri, type=base.name or 'anonymous'),)


class BoundedInteger:
    meta: ClassVar = RuleMeta(
        'bounded-integer',
        Category.SECURITY,
        'integers should have minimum and maximum',
        (
            'OWASP API4:2023 Unrestricted Resource Consumption. OWASP asks for validation of parameters, '
            'especially one that controls how many records are returned. `minimum` and `maximum` prevent a '
            'negative count or a request for a million iterations when ten were expected.'
        ),
        Severity.WARNING,
        good=_request_type('    type: integer\n    minimum: 0\n    maximum: 10\n'),
        bad=_request_type('    type: integer\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if (
            not isinstance(shape, IntegerShape)
            or (shape.minimum is not None and shape.maximum is not None)
            or not _is_input(ctx, iri, base)
        ):
            return ()
        return (
            ctx.on(self.meta, 'integer lacks a lower or upper bound', base, iri=iri, type=base.name or 'anonymous'),
        )


class NoAdditionalProperties:
    meta: ClassVar = RuleMeta(
        'no-additional-properties',
        Category.SECURITY,
        'objects should not explicitly permit extras',
        (
            'OWASP API3:2023 Broken Object Property Level Authorization. Accepting undeclared properties invites '
            'mass assignment, where a client sets fields such as a role or a price that it should not '
            'control. OWASP asks that only client-updatable properties be accepted.'
        ),
        Severity.WARNING,
        good=_request_type('    type: object\n    additionalProperties: false\n'),
        bad=_request_type('    type: object\n    additionalProperties: true\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if (
            not isinstance(shape, ObjectShape)
            or not _is_input(ctx, iri, base)
            or shape.additional_properties is None
            or not shape.additional_properties.value
        ):
            return ()
        facet = shape.additional_properties
        return (
            ctx.on(
                self.meta,
                'object explicitly permits additional properties',
                facet,
                iri=iri,
                type=base.name or 'anonymous',
            ),
        )


class BoundedAdditionalProperties:
    meta: ClassVar = RuleMeta(
        'bounded-additional-properties',
        Category.SECURITY,
        'open objects should limit their total property count',
        (
            'OWASP API3:2023 Broken Object Property Level Authorization and API4:2023 Unrestricted Resource '
            'Consumption. An open object accepts unlimited extra fields: a mass-assignment risk, and an unlimited '
            'payload. RAML has no separate bound on additional properties, so `maxProperties` limits the '
            "object's total property count."
        ),
        Severity.WARNING,
        good=_request_type('    type: object\n    additionalProperties: true\n    maxProperties: 20\n'),
        bad=_request_type('    type: object\n    additionalProperties: true\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if (
            not isinstance(shape, ObjectShape)
            or (shape.additional_properties is not None and not shape.additional_properties.value)
            or shape.max_properties is not None
            or not _is_input(ctx, iri, base)
        ):
            return ()
        return (
            ctx.on(
                self.meta, 'open object has no maximum property count', base, iri=iri, type=base.name or 'anonymous'
            ),
        )
