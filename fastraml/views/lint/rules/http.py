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
    'ProxyAuthenticate407',
    'RedirectLocation',
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
    meta = RuleMeta(
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
    meta = RuleMeta(
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
    meta = RuleMeta(
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
