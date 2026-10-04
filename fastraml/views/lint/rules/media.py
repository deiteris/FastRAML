"""Body media types a RAML document can declare and their RFCs do not allow as written.

The parser checks that a body key is a media type. It does not know that type
and subtype names are case-insensitive, so two keys differing only in case
declare one representation twice, nor that JSON defines no `charset`. These
rules are in the opt-in `http` set and cite their clause in `references`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.parser.facets import media_parts
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity
from fastraml.views.lint.mediatypes import is_json
from fastraml.views.lint.messages import messages

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import Body, Operation
    from fastraml.views.lint.engine import Context

__all__ = ['DuplicateMediaType', 'JsonCharset']

_UTF8: Final = frozenset({'utf-8', 'utf8'})


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
        essence, parameters = media_parts(body.media_type)
        charset = dict(parameters).get('charset')
        if charset is None or not is_json(essence):
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
        for where, message in messages(operation):
            seen: dict[tuple[str, frozenset[tuple[str, str]]], str] = {}
            for media_type, body in message.bodies.items():
                first = seen.setdefault(media_parts(media_type), media_type)
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
