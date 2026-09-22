"""Security rules from OWASP and the OAuth RFCs, translated onto RAML's effective model."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.parser.fragments import APIFragment
from fastraml.parser.security import TYPE_BASIC, TYPE_OAUTH1, TYPE_OAUTH2
from fastraml.parser.uritemplates import extract_uri_template_params
from fastraml.types.complex_ import ArrayShape, ObjectShape
from fastraml.types.scalars import AnyShape, DateTimeShape, FileShape, IntegerShape, NumberShape, StringShape
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
    'BoundedFile',
    'BoundedInteger',
    'BoundedNumber',
    'CredentialInQuery',
    'HttpsOnly',
    'InsecureBasicAuthentication',
    'IntegerFormat',
    'NestedQuantifierPattern',
    'NoAdditionalProperties',
    'NumericResourceId',
    'OAuth1Scheme',
    'OAuth2InsecureGrant',
    'OAuthEndpointHttps',
    'RateLimitHeaders',
    'Required401Response',
    'Required429Response',
    'Required500Response',
    'RestrictedFileTypes',
    'RestrictedRequestMediaType',
    'RestrictedString',
    'RetryAfter429',
    'UnanchoredStringPattern',
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


# -- reading a RAML regular expression -------------------------------------------
#
# Both pattern rules need the expression's structure, not its behaviour: where
# its anchors sit, and whether a quantified group holds another unbounded
# quantifier. One tokenizer serves both. A run of literals is one token, so the
# loops below step over structure rather than over characters (docs/12 § 12).

_REGEX_TOKEN: Final = re.compile(
    r'\\.'  # an escape
    r'|\[\^?\]?(?:\\.|[^\]\\])*\]'  # a character class
    r'|\{\d+(?:,\d*)?\}'  # a counted quantifier
    r'|\((?:\?(?:[:=!>]|<[=!]|P?<\w+>|[aiLmsux-]*:))?'  # a group opener, prefix included
    r'|[)|^$*+?]'  # structure
    r'|(?:[^\\\[()|^$*+?{](?![*+?{]))+'  # a run of literals no quantifier applies to
    r'|.',  # one literal, which a quantifier may follow
    re.DOTALL,
)
_QUANTIFIERS: Final = frozenset({'*', '+', '?'})
#: Global inline flags at the very start. `m` makes `^` and `$` match at every
#: line, so an expression carrying it is not anchored to the whole value.
_LEADING_FLAGS: Final = re.compile(r'\(\?([aiLmsux]+)\)')
_START_ANCHORS: Final = frozenset({'^', r'\A'})
_END_ANCHORS: Final = frozenset({'$', r'\Z', r'\z'})


def _pattern_source(shape: StringShape) -> str:
    """The expression as written. `re` and `re2` both expose `.pattern`."""
    return '' if shape.pattern is None else str(getattr(shape.pattern.value, 'pattern', ''))


def _fully_anchored(pattern: str) -> bool:
    """Every top-level alternative starts at a start anchor and ends at an end anchor.

    `^a|b$` anchors each branch at one end only, and `(?m)^a$` anchors to a
    line; both are unanchored here. A group wrapping the anchors, `(^a$)`, is
    also reported: rare, and cheap to rewrite as `^(a)$`.
    """
    flags = _LEADING_FLAGS.match(pattern)
    if flags is not None:
        if 'm' in flags.group(1):
            return False
        pattern = pattern[flags.end() :]
    branches: list[list[str]] = [[]]
    depth = 0
    for piece in _REGEX_TOKEN.findall(pattern):
        if piece.startswith('('):
            depth += 1
        elif piece == ')':
            depth -= 1
        elif piece == '|' and depth == 0:
            branches.append([])
            continue
        branches[-1].append(piece)
    return all(branch and branch[0] in _START_ANCHORS and branch[-1] in _END_ANCHORS for branch in branches)


def _is_quantifier(piece: str) -> bool:
    return piece in _QUANTIFIERS or (piece.startswith('{') and piece.endswith('}'))


def _unbounded(piece: str) -> bool:
    return piece in {'*', '+'} or (piece.startswith('{') and piece.endswith(',}'))


class _Group:
    """What one group's direct content can do, for `_nested_quantifier`."""

    __slots__ = ('mandatory', 'unbounded')

    def __init__(self) -> None:
        #: Something inside must match exactly once: a separator, such as `-` in `(-[a-z]+)*`.
        self.mandatory = False
        #: Something inside repeats without limit.
        self.unbounded = False

    def settle(self, atom: _Group | None) -> None:
        """An unquantified atom: a group passes on what it holds, anything else must match."""
        if atom is None:
            self.mandatory = True
        else:
            self.mandatory |= atom.mandatory
            self.unbounded |= atom.unbounded


def _nested_quantifier(pattern: str) -> bool:
    r"""An unboundedly repeated group whose content repeats and has no separator: `(a+)+`, `(\w+\s?)*`.

    A separator that must match once per repetition, as in `(-[a-z]+)*`, fixes
    where each repetition starts, so the group is not reported. That misses a
    separator the repeated part can also match; this is a heuristic.
    """
    stack = [_Group()]
    pending = False  # an atom is waiting to learn whether a quantifier follows
    atom: _Group | None = None  # that atom, when it is a group
    for piece in _REGEX_TOKEN.findall(pattern):
        frame = stack[-1]
        if _is_quantifier(piece):
            if pending and _unbounded(piece):
                if atom is not None and atom.unbounded and not atom.mandatory:
                    return True
                frame.unbounded = True
            elif pending and atom is not None:
                frame.unbounded |= atom.unbounded
            pending, atom = False, None
            continue
        if pending:
            frame.settle(atom)
            pending, atom = False, None
        if piece.startswith('('):
            stack.append(_Group())
        elif piece == ')':
            if len(stack) > 1:
                atom, pending = stack.pop(), True
        elif piece not in {'|', '^', '$'}:
            pending = True
    return False


class InsecureBasicAuthentication:
    meta: ClassVar = RuleMeta(
        'insecure-basic-authentication',
        Category.SECURITY,
        'Basic Authentication is declared',
        (
            'Basic sends the long-lived password itself, base64-encoded '
            'rather than encrypted, on every request, so each request exposes a reusable credential. Prefer a '
            'token-based scheme such as OAuth 2.0.'
        ),
        Severity.WARNING,
        references=('OWASP API2:2023', 'CWE-522'),
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
            'Missing TLS is a listed misconfiguration, and OWASP asks '
            'that all API communication use an encrypted channel, whether the API is internal or public.'
        ),
        Severity.WARNING,
        references=('OWASP API8:2023', 'CWE-319'),
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
    meta: ClassVar = RuleMeta(
        'required-401-response',
        Category.SECURITY,
        'operations should document a 401 body',
        (
            'OWASP asks that every response payload schema be defined '
            'and enforced, including error responses, so that exception traces and other internal details are '
            'not sent back to attackers. A 401 is the error every authenticated operation can return.'
        ),
        Severity.WARNING,
        references=('OWASP API8:2023', 'CWE-209'),
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
    meta: ClassVar = RuleMeta(
        'required-429-response',
        Category.SECURITY,
        'operations should document a 429 body',
        (
            'OWASP asks for a limit on how often a client can '
            'call the API. A typed 429 documents that the limit exists and lets clients recognise it and back '
            'off, instead of retrying at once.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'RFC 6585 § 4'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      429:\n        body:\n          application/json: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n',
    )


class Required500Response(_RequiredResponse):
    status = '500'
    meta: ClassVar = RuleMeta(
        'required-500-response',
        Category.SECURITY,
        'operations should document a 500 body',
        (
            'Error messages that include stack traces are a listed '
            'misconfiguration. OWASP asks that error response schemas be defined and enforced. An unexpected '
            'server failure is where an undefined 500 most often leaks one.'
        ),
        Severity.WARNING,
        references=('OWASP API8:2023', 'CWE-209'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      500:\n        body:\n          application/json: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n',
    )


class ValidationErrorResponse:
    meta: ClassVar = RuleMeta(
        'validation-error-response',
        Category.SECURITY,
        'operations should document a 400 or 422 body',
        (
            'OWASP asks that error response schemas be defined and '
            'enforced. An operation that accepts input can reject it, and a typed 400 or 422 says what the '
            'rejection exposes instead of leaving it to the framework default.'
        ),
        Severity.WARNING,
        references=('OWASP API8:2023', 'CWE-209'),
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
            'OWASP asks for a limit on how often a client can '
            'call the API. Rate-limit headers document that limit and let well-behaved clients throttle '
            'themselves before they reach it.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-770'),
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
            'Retry-After tells a rate-limited client when to '
            'retry, so clients do not poll immediately and add to the load that caused the limit.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'RFC 6585 § 4', 'RFC 9110 § 10.2.3'),
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
            'Sequential numeric IDs let an attacker enumerate '
            "other users' records, so OWASP prefers random, unpredictable values. This is an extra layer of "
            'defence: the fix OWASP requires is an authorization check on every record access.'
        ),
        Severity.WARNING,
        references=('OWASP API1:2023', 'CWE-639'),
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
            'OWASP asks for a maximum number of elements in '
            'every incoming array. `maxItems` limits the memory and processing an attacker-supplied array can '
            'demand. Constrain the `items` type as well.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-770'),
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
            'OWASP asks for server-side validation of query '
            'and body parameters. A `pattern` or `enum` states which values are accepted, so arbitrary text is '
            'not accepted by default.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-20'),
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
            'OWASP asks for a maximum length on every incoming '
            'string. A string with no size or value restriction allows unlimited memory use wherever the shape '
            'is used as input. Checking the shape rather than only its current use sites also '
            'covers named types before they are wired into an endpoint. `maxLength` supplies a direct bound; '
            '`pattern` and `enum` record an intentional accepted domain.'
        ),
        severity=Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-770'),
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
            'An explicit `format` fixes the range a server '
            'must accept. Without it, generators and validators each choose a width, and an out-of-range value '
            'can overflow or be accepted by one and rejected by another.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-190'),
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
            'OWASP asks for validation of parameters, '
            'especially one that controls how many records are returned. `minimum` and `maximum` prevent a '
            'negative count or a request for a million iterations when ten were expected.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-1284'),
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
            'Accepting undeclared properties invites '
            'mass assignment, where a client sets fields such as a role or a price that it should not '
            'control. OWASP asks that only client-updatable properties be accepted.'
        ),
        Severity.WARNING,
        references=('OWASP API3:2023', 'CWE-915'),
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
            'An open object accepts unlimited extra fields: a mass-assignment risk, and an unlimited '
            'payload. RAML has no separate bound on additional properties, so `maxProperties` limits the '
            "object's total property count."
        ),
        Severity.WARNING,
        references=('OWASP API3:2023', 'OWASP API4:2023', 'CWE-915', 'CWE-770'),
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


def _upload_type(declaration: str) -> str:
    return (
        f'#%RAML 1.0\ntitle: t\ntypes:\n  Upload:\n{declaration}'
        '/a:\n  post:\n    body:\n      application/octet-stream: Upload\n'
    )


class BoundedNumber:
    meta: ClassVar = RuleMeta(
        'bounded-number',
        Category.SECURITY,
        'numbers should have minimum and maximum',
        (
            'OWASP asks for server-side validation of parameters and payloads. A number with no range accepts '
            'any value, such as `1e308` or a negative amount, that no business rule means to allow. `integer` '
            'has its own rule, `bounded-integer`.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-1284'),
        good=_request_type('    type: number\n    minimum: 0\n    maximum: 100\n'),
        bad=_request_type('    type: number\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if (
            not isinstance(shape, NumberShape)
            or (shape.minimum is not None and shape.maximum is not None)
            or not _is_input(ctx, iri, base)
        ):
            return ()
        return (ctx.on(self.meta, 'number lacks a lower or upper bound', base, iri=iri, type=base.name or 'anonymous'),)


class BoundedFile:
    meta: ClassVar = RuleMeta(
        'bounded-file',
        Category.SECURITY,
        'file uploads should have maxLength',
        (
            'OWASP asks for a maximum upload file size. `maxLength` on a `file` type is that bound, in bytes; '
            'without it the contract accepts an upload of any size.'
        ),
        Severity.WARNING,
        references=('OWASP API4:2023', 'CWE-400'),
        good=_upload_type('    type: file\n    maxLength: 1048576\n'),
        bad=_upload_type('    type: file\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if not isinstance(base.shape, FileShape) or base.shape.max_length is not None or not _is_input(ctx, iri, base):
            return ()
        return (ctx.on(self.meta, 'file has no maximum length', base, iri=iri, type=base.name or 'anonymous'),)


class RestrictedFileTypes:
    meta: ClassVar = RuleMeta(
        'restricted-file-types',
        Category.SECURITY,
        'file uploads should list their accepted media types',
        (
            'The OWASP File Upload Cheat Sheet asks for an allowlist of accepted file types. `fileTypes` is that '
            'list; without it, or with `*/*`, the contract accepts any content, including files a later reader '
            'may execute or render. The list documents the allowlist; the server must still check the content, '
            'because a client chooses the media type it declares.'
        ),
        Severity.WARNING,
        references=('OWASP File Upload Cheat Sheet', 'CWE-434'),
        good=_upload_type('    type: file\n    fileTypes: [image/png, image/jpeg]\n'),
        bad=_upload_type('    type: file\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if not isinstance(shape, FileShape) or not _is_input(ctx, iri, base):
            return ()
        declared = [facet.value for facet in shape.file_types or ()]
        if declared and '*/*' not in declared:
            return ()
        return (
            ctx.on(
                self.meta,
                'file accepts every media type',
                base,
                iri=iri,
                type=base.name or 'anonymous',
                fileTypes=','.join(declared) or 'unspecified',
            ),
        )


class UnanchoredStringPattern:
    meta: ClassVar = RuleMeta(
        'unanchored-string-pattern',
        Category.SECURITY,
        'input string patterns should be anchored',
        (
            'RAML tests `pattern` with a search, not a full match, so `[a-z]+` accepts `<script>a` because one '
            'substring matches. On input, an unanchored pattern validates less than it appears to, and the '
            'OWASP Input Validation Cheat Sheet recommends allowlist validation, which a substring match does '
            'not provide. Anchor it, `^...$`, and group an alternation, `^(?:a|b)$`: `^a|b$` anchors each '
            'branch at one end only.'
        ),
        Severity.WARNING,
        references=('OWASP Input Validation Cheat Sheet', 'CWE-625'),
        good=_request_type('    type: string\n    pattern: ^[a-z]+$\n'),
        bad=_request_type('    type: string\n    pattern: "[a-z]+"\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if not isinstance(shape, StringShape) or shape.pattern is None or not _is_input(ctx, iri, base):
            return ()
        pattern = _pattern_source(shape)
        if _fully_anchored(pattern):
            return ()
        return (
            ctx.on(
                self.meta,
                'string pattern is not fully anchored',
                base,
                iri=iri,
                type=base.name or 'anonymous',
                pattern=pattern,
            ),
        )


class NestedQuantifierPattern:
    meta: ClassVar = RuleMeta(
        'nested-quantifier-pattern',
        Category.SECURITY,
        'input string patterns should not nest unbounded quantifiers',
        (
            'A quantified group that holds another unbounded quantifier, as in `(a+)+`, lets a backtracking '
            'engine try exponentially many ways to split a failing input, so one crafted value can hold a worker '
            'for seconds or longer. This is a heuristic. A group with a separator that must match once per '
            'repetition, as in `(-[a-z]+)*`, is not reported, and overlapping alternatives such as `(a|a)*` '
            "are not found. It is silent under `regex_engine='re2'`, which matches in linear time."
        ),
        Severity.INFO,
        references=('OWASP API4:2023', 'OWASP Regular expression Denial of Service', 'CWE-1333'),
        good=_request_type('    type: string\n    pattern: ^[a-z]+(-[a-z]+)*$\n'),
        bad=_request_type('    type: string\n    pattern: ^([a-z]+)+$\n'),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if (
            ctx.raml.regex_engine == 're2'
            or not isinstance(shape, StringShape)
            or shape.pattern is None
            or not _is_input(ctx, iri, base)
        ):
            return ()
        pattern = _pattern_source(shape)
        if not _nested_quantifier(pattern):
            return ()
        return (
            ctx.on(
                self.meta,
                'string pattern nests unbounded quantifiers',
                base,
                iri=iri,
                type=base.name or 'anonymous',
                pattern=pattern,
            ),
        )


class RestrictedRequestMediaType:
    meta: ClassVar = RuleMeta(
        'restricted-request-media-type',
        Category.SECURITY,
        'request bodies should name concrete media types',
        (
            'OWASP asks that incoming content types be restricted to the formats the business needs. A wildcard '
            'request media type accepts every format, so the server has to parse whatever a client sends, '
            'including formats whose parsers it never meant to expose.'
        ),
        Severity.WARNING,
        references=('OWASP API8:2023',),
        good='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json: string\n',
        bad="#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      '*/*': string\n",
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        request = operation.request
        if request is None:
            return ()
        return [
            ctx.on(
                self.meta,
                'request body accepts a wildcard media type',
                body,
                iri=iri,
                method=operation.method,
                mediaType=media_type,
            )
            for media_type, body in request.bodies.items()
            if '*' in media_type
        ]


class CredentialInQuery:
    meta: ClassVar = RuleMeta(
        'credential-in-query',
        Category.SECURITY,
        'security schemes should not send credentials in the query string',
        (
            "A security scheme's `describedBy` declares what the scheme sends, so its query parameters are "
            'credentials by construction and no name has to be guessed. A URL is written to server, proxy and '
            'browser logs and history, which exposes any credential in it. RFC 6750 says a bearer token SHOULD '
            'NOT travel in the query unless no header or body can carry it. Send credentials in a header.'
        ),
        Severity.WARNING,
        references=('OWASP API2:2023', 'RFC 6750 § 2.3', 'CWE-598'),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  token:\n    type: Pass Through\n'
            '    describedBy:\n      headers:\n        Authorization: string\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  token:\n    type: Pass Through\n'
            '    describedBy:\n      queryParameters:\n        access_token: string\n'
        ),
    )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        described = definition.resolved().described_by
        if described is None:
            return ()
        message = 'security scheme sends a credential in the query string'
        found = [
            ctx.on(
                self.meta,
                message,
                parameter.base,
                iri=iri,
                position=parameter.key_pos,
                scheme=definition.name,
                parameter=name,
            )
            for name, parameter in described.query_parameters.items()
        ]
        if described.query_string is not None:
            found.append(
                ctx.on(
                    self.meta, message, described.query_string, iri=iri, scheme=definition.name, parameter='queryString'
                )
            )
        return found


#: Grants RFC 9700 retires, with the clause that does it.
_INSECURE_GRANTS: Final = {'password': 'RFC 9700 § 2.4', 'implicit': 'RFC 9700 § 2.1.2'}


class OAuth2InsecureGrant:
    meta: ClassVar = RuleMeta(
        'oauth2-insecure-grant',
        Category.SECURITY,
        'OAuth 2.0 schemes should not offer the password or implicit grant',
        (
            'RFC 9700, the OAuth 2.0 Security Best Current Practice, says the resource owner password '
            "credentials grant MUST NOT be used, because it hands the user's credentials to the client, and "
            'that clients SHOULD NOT use the implicit grant, because it returns the access token in the '
            'redirect, where it can leak and be replayed. Use the authorization code grant.'
        ),
        Severity.WARNING,
        references=('RFC 9700 § 2.1.2', 'RFC 9700 § 2.4', 'OWASP API2:2023'),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://example.com/token\n      authorizationGrants: [client_credentials]\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://example.com/token\n      authorizationGrants: [password]\n'
        ),
    )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        resolved = definition.resolved()
        if resolved.type != TYPE_OAUTH2 or resolved.settings is None:
            return ()
        grants = dict.fromkeys(resolved.settings.lists.get('authorizationGrants', ()))
        return [
            ctx.on(
                self.meta,
                'OAuth 2.0 scheme offers a retired grant',
                definition,
                iri=iri,
                scheme=definition.name,
                grant=grant,
                clause=_INSECURE_GRANTS[grant],
            )
            for grant in grants
            if grant in _INSECURE_GRANTS
        ]


_OAUTH_ENDPOINTS: Final = ('requestTokenUri', 'authorizationUri', 'tokenCredentialsUri', 'accessTokenUri')


class OAuthEndpointHttps:
    meta: ClassVar = RuleMeta(
        'oauth-endpoint-https',
        Category.SECURITY,
        'OAuth endpoints should use HTTPS',
        (
            'RFC 6749 requires TLS at the authorization and token endpoints, because both carry credentials in '
            "the clear: the user's at the authorization endpoint, and the client's and the grant at the token "
            'endpoint. The same holds for the OAuth 1.0 endpoints. A document naming an `http:` endpoint tells '
            'every client to send those credentials unencrypted.'
        ),
        Severity.WARNING,
        references=('RFC 6749 § 3.1', 'RFC 6749 § 3.2', 'OWASP API8:2023', 'CWE-319'),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://example.com/token\n      authorizationGrants: [client_credentials]\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: http://example.com/token\n      authorizationGrants: [client_credentials]\n'
        ),
    )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        resolved = definition.resolved()
        if resolved.type not in {TYPE_OAUTH1, TYPE_OAUTH2} or resolved.settings is None:
            return ()
        values = resolved.settings.values
        return [
            ctx.on(
                self.meta,
                'OAuth endpoint does not use HTTPS',
                facet,
                iri=iri,
                scheme=definition.name,
                setting=key,
                uri=facet.value,
            )
            for key in _OAUTH_ENDPOINTS
            if (facet := values.get(key)) is not None and facet.value.partition(':')[0].casefold() == 'http'
        ]


class OAuth1Scheme:
    meta: ClassVar = RuleMeta(
        'oauth1-scheme',
        Category.SECURITY,
        'OAuth 1.0 is declared',
        (
            'RFC 6749 obsoletes OAuth 1.0 (RFC 5849), and the current OAuth security guidance, RFC 9700, covers '
            'OAuth 2.0 only. OAuth 1.0 signs requests with SHA-1 or not at all: its `PLAINTEXT` method relies '
            'entirely on TLS. Use OAuth 2.0.'
        ),
        Severity.WARNING,
        references=('RFC 6749', 'RFC 5849 § 3.4.4', 'OWASP API2:2023'),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 2.0\n    settings:\n'
            '      accessTokenUri: https://example.com/token\n      authorizationGrants: [client_credentials]\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  oauth:\n    type: OAuth 1.0\n    settings:\n'
            '      requestTokenUri: https://example.com/request\n'
            '      authorizationUri: https://example.com/authorize\n'
            '      tokenCredentialsUri: https://example.com/token\n'
        ),
    )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        resolved = definition.resolved()
        if resolved.type != TYPE_OAUTH1:
            return ()
        signatures = [] if resolved.settings is None else resolved.settings.lists.get('signatures', [])
        return (
            ctx.on(
                self.meta,
                'security scheme uses OAuth 1.0',
                definition,
                iri=iri,
                scheme=definition.name,
                signatures=','.join(signatures) or 'unspecified',
            ),
        )
