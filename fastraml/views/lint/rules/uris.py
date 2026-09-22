"""Resource and base URIs that RFC 3986 does not allow as written.

RAML accepts any text as a resource key and any string as a `baseUri`. RFC
3986 fixes which characters a path segment may hold, removes `.` and `..`
segments before a request is sent, and deprecates a password in the
authority. The path rules are in the `http` set, whose URIs RFC 9110 § 4.2
defines through RFC 3986; the credential rule is in `security`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final
from urllib.parse import urlsplit

from fastraml.parser.fragments import APIFragment
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import EndPoint
    from fastraml.parser.fragments import Fragment
    from fastraml.views.lint.engine import Context

__all__ = ['BaseUriUserinfo', 'DotSegmentPath', 'UriPathCharacters']

#: A template expression: RFC 6570 syntax, which the parser has already validated.
_EXPRESSION: Final = re.compile(r'\{[^{}]*\}')
#: RFC 3986 § 3.3: `pchar = unreserved / pct-encoded / sub-delims / ":" / "@"`.
_PCHARS: Final = re.compile(r"(?:[A-Za-z0-9\-._~!$&'()*+,;=:@]|%[0-9A-Fa-f]{2})*")
_DOT_SEGMENTS: Final = frozenset({'.', '..'})


def _own_segments(endpoint: EndPoint) -> list[str]:
    """The segments this resource adds; a child's full path repeats its parent's."""
    return endpoint.uri.split('/')[1:]


class UriPathCharacters:
    meta: ClassVar = RuleMeta(
        'uri-path-characters',
        Category.HTTP,
        'resource paths should hold only characters a URI path allows',
        (
            "A path segment is letters, digits, `-._~`, the sub-delimiters `!$&'()*+,;=`, `:`, `@` and "
            'percent-encoded octets. A space, a non-ASCII letter or a character such as `<`, `|` or `"` must be '
            'percent-encoded to travel, so the resource as written is not the URI a client sends and a server '
            'matches.'
        ),
        Severity.WARNING,
        references=('RFC 3986 § 3.3', 'RFC 3986 § 2.1'),
        good='#%RAML 1.0\ntitle: t\n/order-items:\n  get:\n',
        bad="#%RAML 1.0\ntitle: t\n'/order items':\n  get:\n",
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        bad = [
            segment for segment in _own_segments(endpoint) if _PCHARS.fullmatch(_EXPRESSION.sub('', segment)) is None
        ]
        if not bad:
            return ()
        return (
            ctx.on(
                self.meta,
                'resource path holds characters a URI path does not allow',
                endpoint,
                iri=iri,
                path=endpoint.full_uri,
                segments=','.join(bad),
            ),
        )


class DotSegmentPath:
    meta: ClassVar = RuleMeta(
        'dot-segment-path',
        Category.HTTP,
        'resource paths should not contain `.` or `..` segments',
        (
            'The complete segments `.` and `..` are removed during reference resolution, and URI normalizers '
            'remove them from absolute paths too. A client therefore never sends the path as written, and a '
            'resource declared with one cannot be reached at the URI the contract gives it.'
        ),
        Severity.WARNING,
        references=('RFC 3986 § 5.2.4', 'RFC 3986 § 6.2.2.3'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  /b:\n    get:\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  /..:\n    get:\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        if not any(segment in _DOT_SEGMENTS for segment in _own_segments(endpoint)):
            return ()
        return (ctx.on(self.meta, 'resource path contains a dot segment', endpoint, iri=iri, path=endpoint.full_uri),)


class BaseUriUserinfo:
    meta: ClassVar = RuleMeta(
        'base-uri-userinfo',
        Category.SECURITY,
        'baseUri should not carry a password',
        (
            "Using the `user:password` form in a URI's authority is deprecated, and a contract that writes one "
            'publishes the credential to everyone who reads the document and every log that records the URI. '
            'Authenticate through a security scheme instead.'
        ),
        Severity.WARNING,
        references=('RFC 3986 § 3.2.1', 'OWASP API2:2023', 'CWE-798'),
        good='#%RAML 1.0\ntitle: t\nbaseUri: https://api.example.com/v1\n',
        bad='#%RAML 1.0\ntitle: t\nbaseUri: https://user:secret@api.example.com/v1\n',
    )

    def unit(self, ctx: Context, iri: str, fragment: Fragment) -> Iterable[Finding]:
        if not isinstance(fragment, APIFragment) or fragment.base_uri is None:
            return ()
        userinfo, at, _ = urlsplit(fragment.base_uri.value).netloc.rpartition('@')
        if not at or ':' not in userinfo:
            return ()
        return (
            ctx.on(
                self.meta,
                'baseUri carries a password in its authority',
                fragment.base_uri,
                iri=iri,
                user=userinfo.partition(':')[0],
            ),
        )
