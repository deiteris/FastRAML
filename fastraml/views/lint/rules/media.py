"""Body media types a RAML document can declare and their RFCs do not allow as written.

The parser checks that a body key is a media type. It does not know that type
and subtype names are case-insensitive, so two keys differing only in case
declare one representation twice, nor that JSON defines no `charset`. These
rules are in the opt-in `http` set and cite their clause in `references`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from fastraml.parser.endpoints import Body, Operation
    from fastraml.views.lint.engine import Context

__all__ = ['DuplicateMediaType', 'JsonCharset']

_UTF8: Final = frozenset({'utf-8', 'utf8'})


def _split(media_type: str) -> tuple[str, dict[str, str]]:
    """`type/subtype`, lowercased, and its parameters by lowercased name."""
    essence, *parameters = (part.strip() for part in media_type.split(';'))
    found = {}
    for parameter in parameters:
        name, _, value = parameter.partition('=')
        found[name.strip().casefold()] = value.strip().strip('"')
    return essence.casefold(), found


def _is_json(essence: str) -> bool:
    return essence == 'application/json' or essence.endswith('+json')


class JsonCharset:
    meta: ClassVar = RuleMeta(
        'json-charset',
        Category.HTTP,
        'JSON media types should not carry a charset parameter',
        (
            'JSON exchanged between systems MUST be UTF-8, and `application/json` defines no `charset` '
            'parameter: adding one "really has no effect on compliant recipients". `charset=utf-8` is '
            'therefore noise, and any other charset declares an encoding the format forbids.'
        ),
        Severity.WARNING,
        references=('RFC 8259 § 8.1', 'RFC 8259 § 11'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json: string\n',
        bad="#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      'application/json; charset=utf-16': string\n",
    )

    def payload(self, ctx: Context, iri: str, body: Body) -> Iterable[Finding]:
        essence, parameters = _split(body.media_type)
        charset = parameters.get('charset')
        if charset is None or not _is_json(essence):
            return ()
        forbidden = charset.casefold() not in _UTF8
        return (
            ctx.on(
                self.meta,
                'JSON media type carries a charset parameter',
                body,
                iri=iri,
                mediaType=body.media_type,
                charset=charset,
                clause='RFC 8259 § 8.1' if forbidden else 'RFC 8259 § 11',
            ),
        )


def _body_maps(operation: Operation) -> Iterator[tuple[str, Mapping[str, Body]]]:
    if operation.request is not None:
        yield 'request', operation.request.bodies
    for code, response in operation.responses.items():
        yield code, response.bodies


class DuplicateMediaType:
    meta: ClassVar = RuleMeta(
        'duplicate-media-type',
        Category.HTTP,
        'one body map should not declare a media type twice in different case',
        (
            'Top-level type and subtype names are case-insensitive, so `application/json` and '
            '`Application/JSON` are one media type. RAML keys are case-sensitive and accept both, which gives '
            'one representation two schemas that may disagree. Parameter names are compared without case; '
            'their values are compared exactly.'
        ),
        Severity.WARNING,
        references=('RFC 6838 § 4.2', 'RFC 9110 § 8.3.1'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json: string\n',
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json: string\n'
            '      Application/JSON: string\n'
        ),
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        found = []
        for where, bodies in _body_maps(operation):
            seen: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
            for media_type, body in bodies.items():
                essence, parameters = _split(media_type)
                first = seen.setdefault((essence, tuple(sorted(parameters.items()))), media_type)
                if first != media_type:
                    found.append(
                        ctx.on(
                            self.meta,
                            'media type is declared twice',
                            body,
                            iri=iri,
                            mediaType=media_type,
                            duplicates=first,
                            where=where,
                        )
                    )
        return found
