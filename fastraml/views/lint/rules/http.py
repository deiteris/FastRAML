"""HTTP semantics from RFC 9110 that a RAML document can contradict.

RAML accepts any status code with any headers and body. HTTP does not: some
responses cannot carry content, and some must carry a header. Each rule here
cites the clause it follows from in `references`; none of them is a rule of
RAML, which is why they are an opt-in set rather than parse errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fastraml.parser.endpoints import Operation, Response
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.types.base import Parameter
    from fastraml.views.lint.engine import Context

__all__ = [
    'AllowHeader405',
    'ContentRangeHeader',
    'NoContentBody',
    'NotModifiedHeaders',
    'ObsoleteStatusCode',
    'ProxyAuthenticate407',
    'RedirectLocation',
    'UnreachableStatus',
    'WwwAuthenticate401',
]

_INFORMATIONAL: Final = range(100, 200)


def _has_header(headers: Mapping[str, Parameter], name: str) -> bool:
    wanted = name.casefold()
    return any(declared.casefold() == wanted for declared in headers)


def _media_type(value: str) -> str:
    return value.partition(';')[0].strip().casefold()


def _no_content_clause(method: str, code: int) -> str | None:
    """The clause that forbids content in this response, if one does."""
    if code in _INFORMATIONAL:
        return 'RFC 9110 § 15.2'
    if code == 204:  # noqa: PLR2004 - a status code is its own name
        return 'RFC 9110 § 15.3.5'
    if code == 304:  # noqa: PLR2004
        return 'RFC 9110 § 15.4.5'
    if method == 'head':
        return 'RFC 9110 § 9.3.2'
    return None


class NoContentBody:
    meta: ClassVar = RuleMeta(
        'no-content-body',
        Category.HTTP,
        'responses HTTP ends at the header section should declare no body',
        (
            'A 1xx, 204 or 304 response ends at its header section and "cannot contain content", and a server '
            'MUST NOT send content in a response to HEAD. A body declared there describes bytes no conforming '
            'server sends and no client reads, so a generator or mock built from the contract disagrees with '
            'the wire.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 9.3.2', 'RFC 9110 § 15.2', 'RFC 9110 § 15.3.5', 'RFC 9110 § 15.4.5'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  delete:\n    responses:\n      204:\n',
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  delete:\n    responses:\n      204:\n'
            '        body:\n          application/json: string\n'
        ),
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        found = []
        for code, response in operation.responses.items():
            clause = _no_content_clause(operation.method, int(code))
            if clause is None or not response.bodies:
                continue
            found.append(
                ctx.on(
                    self.meta,
                    'response declares a body HTTP forbids',
                    response,
                    iri=iri,
                    method=operation.method,
                    status=code,
                    clause=clause,
                )
            )
        return found


class _RequiredHeader:
    """A status whose response HTTP says carries one header."""

    statuses: ClassVar[frozenset[int]]
    header: ClassVar[str]
    meta: ClassVar[RuleMeta]

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        if int(response.code) not in self.statuses or _has_header(response.headers, self.header):
            return ()
        return (
            ctx.on(
                self.meta,
                'response does not declare the header HTTP specifies for it',
                response,
                iri=iri,
                status=response.code,
                header=self.header,
            ),
        )


class AllowHeader405(_RequiredHeader):
    statuses = frozenset({405})
    header = 'Allow'
    meta: ClassVar = RuleMeta(
        'allow-header-405',
        Category.HTTP,
        '405 responses should declare Allow',
        (
            'An origin server MUST send an Allow header in a 405 response, listing the methods the resource '
            'does support. It is the one place a client learns what it may send instead.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.5.6', 'RFC 9110 § 10.2.1'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    responses:\n      405:\n        headers:\n          Allow: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    responses:\n      405: {}\n',
    )


class ProxyAuthenticate407(_RequiredHeader):
    statuses = frozenset({407})
    header = 'Proxy-Authenticate'
    meta: ClassVar = RuleMeta(
        'proxy-authenticate-407',
        Category.HTTP,
        '407 responses should declare Proxy-Authenticate',
        (
            'A proxy answering 407 MUST send a Proxy-Authenticate header with a challenge for that proxy. '
            'Without it the client cannot tell which scheme to authenticate with.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.5.8', 'RFC 9110 § 11.7.1'),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      407:\n'
            '        headers:\n          Proxy-Authenticate: string\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      407: {}\n',
    )


class RedirectLocation(_RequiredHeader):
    statuses = frozenset({301, 302, 307, 308})
    header = 'Location'
    meta: ClassVar = RuleMeta(
        'redirect-location',
        Category.HTTP,
        'redirects should declare Location',
        (
            'For 301, 302, 307 and 308 the server SHOULD send a Location header naming the target URI; it is '
            'the redirect. A redirect whose contract has no Location leaves clients nowhere to go.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.4.2', 'RFC 9110 § 15.4.3', 'RFC 9110 § 15.4.8', 'RFC 9110 § 15.4.9'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      301:\n        headers:\n          Location: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      301: {}\n',
    )


class ContentRangeHeader:
    meta: ClassVar = RuleMeta(
        'content-range-header',
        Category.HTTP,
        '206 and 416 responses should declare Content-Range',
        (
            'A single-part 206 MUST carry a Content-Range header saying which range it holds; a multi-part 206 '
            'instead carries `multipart/byteranges` content, with a Content-Range in each part. A 416 to a '
            "byte-range request SHOULD carry Content-Range with the representation's current length, so the "
            'client can ask again for a range that exists.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.3.7', 'RFC 9110 § 15.5.17', 'RFC 9110 § 14.4'),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      206:\n'
            '        headers:\n          Content-Range: string\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      206: {}\n',
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        code = int(response.code)
        if code not in {206, 416} or _has_header(response.headers, 'Content-Range'):
            return ()
        if code == 206 and any(_media_type(media) == 'multipart/byteranges' for media in response.bodies):  # noqa: PLR2004
            return ()
        return (
            ctx.on(
                self.meta,
                'response does not declare the header HTTP specifies for it',
                response,
                iri=iri,
                status=response.code,
                header='Content-Range',
            ),
        )


def _challenges(definition: SecuritySchemeDefinition | None) -> bool | None:
    """Whether a scheme's `describedBy` 401 declares WWW-Authenticate; `None` when it declares no 401."""
    described = None if definition is None else definition.resolved().described_by
    unauthorized = None if described is None else described.responses.get('401')
    return None if unauthorized is None else _has_header(unauthorized.headers, 'WWW-Authenticate')


class WwwAuthenticate401:
    meta: ClassVar = RuleMeta(
        'www-authenticate-401',
        Category.HTTP,
        '401 responses should declare WWW-Authenticate',
        (
            'A server generating a 401 MUST send a WWW-Authenticate header with at least one challenge; it is '
            "how a client learns which scheme to answer with. RAML can declare it on the operation's 401 or on "
            "the 401 in a securing scheme's `describedBy`, and either satisfies this rule."
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.5.2', 'RFC 9110 § 11.6.1'),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  token:\n    type: Pass Through\n    describedBy:\n'
            '      headers:\n        Authorization: string\n      responses:\n        401:\n'
            '          headers:\n            WWW-Authenticate: string\n'
            '/a:\n  get:\n    securedBy: [token]\n    responses:\n      401: {}\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      401: {}\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        response = operation.responses.get('401')
        if response is None or _has_header(response.headers, 'WWW-Authenticate'):
            return ()
        if any(_challenges(scheme.definition) for scheme in operation.secured_by):
            return ()
        return (
            ctx.on(
                self.meta,
                'response does not declare the header HTTP specifies for it',
                response,
                iri=iri,
                method=operation.method,
                status=response.code,
                header='WWW-Authenticate',
            ),
        )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        if _challenges(definition) is not False:
            return ()
        described = definition.resolved().described_by
        assert described is not None  # noqa: S101 - `_challenges` returned False, so there is a 401
        return (
            ctx.on(
                self.meta,
                'response does not declare the header HTTP specifies for it',
                described.responses['401'],
                iri=iri,
                scheme=definition.name,
                status='401',
                header='WWW-Authenticate',
            ),
        )


#: Codes RFC 9110 retires, with the clause that does it.
_OBSOLETE_STATUS: Final = {
    305: ('deprecated', 'RFC 9110 § 15.4.6'),
    306: ('unused', 'RFC 9110 § 15.4.7'),
    418: ('unused', 'RFC 9110 § 15.5.19'),
}


class ObsoleteStatusCode:
    meta: ClassVar = RuleMeta(
        'obsolete-status-code',
        Category.HTTP,
        'responses should not use a retired status code',
        (
            '305 Use Proxy is deprecated, and 306 and 418 are reserved and no longer used. A client that meets '
            'one has no current definition to act on, and 418 in particular is reserved so that it can be '
            'assigned to something else later.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.4.6', 'RFC 9110 § 15.4.7', 'RFC 9110 § 15.5.19'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      400: {}\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      418: {}\n',
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        retired = _OBSOLETE_STATUS.get(int(response.code))
        if retired is None:
            return ()
        state, clause = retired
        return (
            ctx.on(
                self.meta,
                'response uses a retired status code',
                response,
                iri=iri,
                status=response.code,
                state=state,
                clause=clause,
            ),
        )


#: What a 304 MUST repeat from the 200 it stands in for (RFC 9110 § 15.4.5).
_NOT_MODIFIED_FIELDS: Final = ('Cache-Control', 'Content-Location', 'Date', 'ETag', 'Expires', 'Vary')


class NotModifiedHeaders:
    meta: ClassVar = RuleMeta(
        'not-modified-headers',
        Category.HTTP,
        'a 304 should declare the validators and cache fields its 200 declares',
        (
            'A server generating a 304 MUST send any of Content-Location, Date, ETag, Vary, Cache-Control and '
            'Expires that the 200 would have carried, because a cache uses them to update the response it '
            'already holds. A 304 declaring fewer of them than its 200 describes a response that breaks the '
            'cache it is meant to refresh.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 15.4.5',),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      If-None-Match: string\n    responses:\n'
            '      200:\n        headers:\n          ETag: string\n'
            '      304:\n        headers:\n          ETag: string\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      If-None-Match: string\n    responses:\n'
            '      200:\n        headers:\n          ETag: string\n'
            '      304: {}\n'
        ),
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        ok, not_modified = operation.responses.get('200'), operation.responses.get('304')
        if ok is None or not_modified is None:
            return ()
        missing = [
            name
            for name in _NOT_MODIFIED_FIELDS
            if _has_header(ok.headers, name) and not _has_header(not_modified.headers, name)
        ]
        if not missing:
            return ()
        return (
            ctx.on(
                self.meta,
                '304 response omits header fields its 200 declares',
                not_modified,
                iri=iri,
                method=operation.method,
                missing=','.join(missing),
            ),
        )


_CONDITIONAL: Final = ('If-Match', 'If-Modified-Since', 'If-None-Match', 'If-Unmodified-Since')
_RANGE_CODES: Final = frozenset({206, 416})
_CONTENT_CODES: Final = {413: 'RFC 9110 § 15.5.14', 415: 'RFC 9110 § 15.5.16'}


def _unreachable(operation: Operation, code: int) -> tuple[str, str] | None:  # noqa: PLR0911 - one exit per status
    """Why this operation can never produce `code`, and the clause; `None` when it can."""
    request = operation.request
    headers = {} if request is None else request.headers
    if code == 304:  # noqa: PLR2004 - a status code is its own name
        if operation.method not in {'get', 'head'}:
            return 'only a conditional GET or HEAD can be answered with 304', 'RFC 9110 § 15.4.5'
        if not any(_has_header(headers, name) for name in ('If-Modified-Since', 'If-None-Match')):
            return 'no If-None-Match or If-Modified-Since request header is declared', 'RFC 9110 § 15.4.5'
    elif code == 412:  # noqa: PLR2004
        if not any(_has_header(headers, name) for name in _CONDITIONAL):
            return 'no precondition request header is declared', 'RFC 9110 § 15.5.13'
    elif code in _RANGE_CODES:
        if operation.method != 'get':
            return 'GET is the only method with range handling', 'RFC 9110 § 14.2'
        if not _has_header(headers, 'Range'):
            return 'no Range request header is declared', 'RFC 9110 § 14.2'
    elif code in _CONTENT_CODES and (request is None or not request.bodies):
        return 'the request declares no content', _CONTENT_CODES[code]
    return None


class UnreachableStatus:
    meta: ClassVar = RuleMeta(
        'unreachable-status',
        Category.HTTP,
        'responses should be reachable from the request the operation declares',
        (
            'Some status codes answer something in the request: 304 and 412 a conditional header, 206 and 416 '
            'a Range header on GET, 413 and 415 the request content. When the operation declares none of it, '
            'the contract lists a response a client following it can never receive. A client may send headers '
            'the contract omits, so this reports at `info`: the fix is usually to declare the request header.'
        ),
        Severity.INFO,
        references=(
            'RFC 9110 § 14.2',
            'RFC 9110 § 15.4.5',
            'RFC 9110 § 15.5.13',
            'RFC 9110 § 15.5.14',
            'RFC 9110 § 15.5.16',
        ),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      Range: string\n'
            '    responses:\n      206:\n        headers:\n          Content-Range: string\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      206:\n'
            '        headers:\n          Content-Range: string\n'
        ),
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        found = []
        for code, response in operation.responses.items():
            why = _unreachable(operation, int(code))
            if why is None:
                continue
            reason, clause = why
            found.append(
                ctx.on(
                    self.meta,
                    'operation declares a response it cannot produce',
                    response,
                    iri=iri,
                    method=operation.method,
                    status=code,
                    reason=reason,
                    clause=clause,
                )
            )
        return found
