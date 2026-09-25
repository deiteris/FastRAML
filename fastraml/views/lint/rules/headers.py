"""Header fields a RAML document can declare and HTTP does not allow as written.

RAML declares a header as a name and a type, and accepts any name and any
type. RFC 9110 fixes a field name's syntax and case-insensitivity, reserves a
set of fields for the connection rather than the message, and gives the date
fields one wire format. These rules are in the opt-in `http` set beside
`http.py`, and each cites its clause in `references`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.types.scalars import DateOnlyShape, DateTimeOnlyShape, DateTimeShape, TimeOnlyShape
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity
from fastraml.views.lint.mediatypes import media_essence

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from fastraml.parser.endpoints import Body, Operation
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.types.base import Parameter
    from fastraml.views.lint.engine import Context

__all__ = ['ContentTypeHeader', 'DuplicateHeader', 'HeaderFieldName', 'HopByHopHeader', 'HttpDateHeader']

#: RFC 9110 § 5.6.2: `token = 1*tchar`.
_TOKEN: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")

#: RFC 9110 § 7.6.1: fields that control the connection, which intermediaries remove.
_HOP_BY_HOP: Final = frozenset({'connection', 'keep-alive', 'proxy-connection', 'te', 'transfer-encoding', 'upgrade'})

#: Fields whose value is an HTTP-date, with the clause that says so. `Retry-After`
#: may also be delta-seconds and has its own rule, `retry-after-429`.
_DATE_FIELDS: Final = {
    'date': 'RFC 9110 § 6.6.1',
    'expires': 'RFC 9111 § 5.3',
    'if-modified-since': 'RFC 9110 § 13.1.3',
    'if-unmodified-since': 'RFC 9110 § 13.1.4',
    'last-modified': 'RFC 9110 § 8.8.2',
    'sunset': 'RFC 8594 § 3',
}


def _header(parameter: Parameter) -> bool:
    return parameter.binding == 'header'


def _header_maps(operation: Operation) -> Iterator[tuple[str, Mapping[str, Parameter]]]:
    """Each header map of one operation, named by where it sits."""
    if operation.request is not None:
        yield 'request', operation.request.headers
    for code, response in operation.responses.items():
        yield code, response.headers


def _scheme_header_maps(definition: SecuritySchemeDefinition) -> Iterator[tuple[str, Mapping[str, Parameter]]]:
    described = definition.resolved().described_by
    if described is None:
        return
    yield 'request', described.headers
    for code, response in described.responses.items():
        yield code, response.headers


class HopByHopHeader:
    meta: ClassVar = RuleMeta(
        'hop-by-hop-header',
        Category.HTTP,
        'contracts should not declare connection-specific header fields',
        (
            '`Connection`, `Keep-Alive`, `Proxy-Connection`, `TE`, `Transfer-Encoding` and `Upgrade` control '
            'one connection, not the message, and every intermediary removes them before forwarding. A client '
            'or server behind a proxy never sees what the contract declares, so they are not part of an API.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 7.6.1',),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      Accept: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      Keep-Alive: string\n',
    )

    def parameter(self, ctx: Context, iri: str, parameter: Parameter) -> Iterable[Finding]:
        if not _header(parameter) or parameter.name.casefold() not in _HOP_BY_HOP:
            return ()
        return (
            ctx.on(
                self.meta,
                'header field is connection-specific',
                parameter.base,
                iri=iri,
                position=parameter.key_pos,
                header=parameter.name,
            ),
        )


class HeaderFieldName:
    meta: ClassVar = RuleMeta(
        'header-field-name',
        Category.HTTP,
        'header names should be HTTP tokens',
        (
            "A field name is a token: letters, digits and ``!#$%&'*+-.^_`|~``. A name with a space, a colon or "
            'any other delimiter cannot be sent in an HTTP message, so the declared header can never arrive.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 5.1', 'RFC 9110 § 5.6.2'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      X-Request-Id: string\n',
        bad="#%RAML 1.0\ntitle: t\n/a:\n  get:\n    headers:\n      'Request Id': string\n",
    )

    def parameter(self, ctx: Context, iri: str, parameter: Parameter) -> Iterable[Finding]:
        if not _header(parameter) or _TOKEN.fullmatch(parameter.name):
            return ()
        return (
            ctx.on(
                self.meta,
                'header name is not an HTTP token',
                parameter.base,
                iri=iri,
                position=parameter.key_pos,
                header=parameter.name,
            ),
        )


class DuplicateHeader:
    meta: ClassVar = RuleMeta(
        'duplicate-header',
        Category.HTTP,
        'one header map should not declare a field twice in different case',
        (
            'Field names are case-insensitive, so `ETag` and `Etag` are one field. RAML keys are '
            'case-sensitive and accept both, which gives the one field two declarations that may disagree.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 5.1',),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        headers:\n          ETag: string\n',
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n'
            '        headers:\n          ETag: string\n          Etag: string\n'
        ),
    )

    def _check(self, ctx: Context, iri: str, maps: Iterable[tuple[str, Mapping[str, Parameter]]]) -> Iterator[Finding]:
        for where, headers in maps:
            seen: dict[str, str] = {}
            for name, parameter in headers.items():
                first = seen.setdefault(name.casefold(), name)
                if first != name:
                    yield ctx.on(
                        self.meta,
                        'header field is declared twice',
                        parameter.base,
                        iri=iri,
                        position=parameter.key_pos,
                        header=name,
                        duplicates=first,
                        where=where,
                    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        return list(self._check(ctx, iri, _header_maps(operation)))

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        return list(self._check(ctx, iri, _scheme_header_maps(definition)))


class HttpDateHeader:
    meta: ClassVar = RuleMeta(
        'http-date-header',
        Category.HTTP,
        'date header fields should be typed as HTTP dates',
        (
            'Date, Expires, Last-Modified, If-Modified-Since, If-Unmodified-Since and Sunset carry an HTTP-date, '
            'such as `Sun, 06 Nov 1994 08:49:37 GMT`. RAML spells that `datetime` with `format: rfc2616`; its '
            'default `datetime` is RFC 3339, and `date-only`, `time-only` and `datetime-only` are not HTTP '
            'dates at all, so a client built from the contract sends a value the server cannot parse. A '
            '`string` is not reported: it constrains nothing, but it does not contradict the format.'
        ),
        Severity.WARNING,
        references=(
            'RFC 9110 § 5.6.7',
            'RFC 9110 § 6.6.1',
            'RFC 9110 § 8.8.2',
            'RFC 9110 § 13.1.3',
            'RFC 9110 § 13.1.4',
            'RFC 9111 § 5.3',
            'RFC 8594 § 3',
        ),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        headers:\n'
            '          Last-Modified:\n            type: datetime\n            format: rfc2616\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        headers:\n'
            '          Last-Modified: datetime\n'
        ),
    )

    def parameter(self, ctx: Context, iri: str, parameter: Parameter) -> Iterable[Finding]:
        clause = _DATE_FIELDS.get(parameter.name.casefold())
        if not _header(parameter) or clause is None:
            return ()
        shape = parameter.base.shape
        if isinstance(shape, DateTimeShape):
            if shape.format is not None and shape.format.value == 'rfc2616':
                return ()
        elif not isinstance(shape, (DateOnlyShape, DateTimeOnlyShape, TimeOnlyShape)):
            return ()
        return (
            ctx.on(
                self.meta,
                'date header field is not typed as an HTTP date',
                parameter.base,
                iri=iri,
                position=parameter.key_pos,
                header=parameter.name,
                type=parameter.base.type,
                clause=clause,
            ),
        )


class ContentTypeHeader:
    meta: ClassVar = RuleMeta(
        'content-type-header',
        Category.HTTP,
        "a declared Content-Type should agree with the body's media types",
        (
            "Content-Type states the media type of the message's content, and in RAML the body's media-type "
            'keys already say what it may be. A declared Content-Type header whose enumeration names a media '
            'type no body has, or one declared where there is no body, contradicts the contract it sits in.'
        ),
        Severity.WARNING,
        references=('RFC 9110 § 8.3',),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    headers:\n      Content-Type:\n'
            '        enum: [application/json]\n    body:\n      application/json: string\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    headers:\n      Content-Type:\n'
            '        enum: [text/plain]\n    body:\n      application/json: string\n'
        ),
    )

    def _check(
        self, ctx: Context, iri: str, where: str, headers: Mapping[str, Parameter], bodies: Mapping[str, Body]
    ) -> Iterator[Finding]:
        declared = next((parameter for name, parameter in headers.items() if name.casefold() == 'content-type'), None)
        if declared is None:
            return
        media = {media_essence(media_type) for media_type in bodies}
        values = [str(member.raw) for member in declared.base.enum or ()]
        stray = [value for value in values if media_essence(value) not in media]
        if bodies and not stray:
            return
        yield ctx.on(
            self.meta,
            'Content-Type header contradicts the body media types',
            declared.base,
            iri=iri,
            position=declared.key_pos,
            where=where,
            declared=','.join(stray) or 'any',
            bodies=','.join(sorted(media)) or 'none',
        )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        found: list[Finding] = []
        if operation.request is not None:
            found.extend(self._check(ctx, iri, 'request', operation.request.headers, operation.request.bodies))
        for code, response in operation.responses.items():
            found.extend(self._check(ctx, iri, code, response.headers, response.bodies))
        return found
