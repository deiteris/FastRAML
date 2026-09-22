"""RFC 7493 I-JSON, for APIs that adopt the profile for their JSON bodies.

I-JSON is a restricted profile of JSON for interoperability: what every
receiver can process exactly. Adopting it is a choice, like problem details,
so this is an opt-in set. It judges only `application/json` and `+json`
bodies, and only RAML-typed ones; a body typed by an included JSON Schema is
left alone.

The rules that look inside a body are document rules: a named type used by
several JSON bodies is one declaration, and it is reported once at the place
it was written rather than once per body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.nodes import PayloadNode
from fastraml.types.complex_ import ArrayShape, ObjectShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import AnyShape, DateTimeOnlyShape, DateTimeShape, FileShape, IntegerShape, NilShape
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from fastraml.parser.endpoints import Body
    from fastraml.types.base import BaseShape
    from fastraml.views.lint.engine import Context

__all__ = ['IJsonBinary', 'IJsonDateTime', 'IJsonIntegerRange', 'IJsonTopLevel']

#: RFC 7493 § 2.2: the integers an IEEE 754 double holds exactly.
_EXACT: Final = 2**53 - 1
_WIDE_FORMATS: Final = frozenset({'int64', 'long'})


def _is_json(media_type: str) -> bool:
    essence = media_type.partition(';')[0].strip().casefold()
    return essence == 'application/json' or essence.endswith('+json')


def _json_bodies(ctx: Context) -> Iterator[tuple[str, Body]]:
    for iri, node in ctx.graph.nodes.items():
        if isinstance(node, PayloadNode) and node.entity.shape is not None and _is_json(node.entity.media_type):
            yield iri, node.entity


def _children(base: BaseShape) -> Iterator[BaseShape]:
    shape = base.shape
    if isinstance(shape, ObjectShape):
        yield from (prop.base for prop in (shape.properties or {}).values())
        yield from (prop.base for prop in (shape.pattern_properties or {}).values())
    elif isinstance(shape, ArrayShape) and shape.items is not None:
        yield shape.items
    elif isinstance(shape, UnionShape):
        yield from shape.any_of or ()


def _json_shapes(ctx: Context) -> Iterator[tuple[str, BaseShape]]:
    """Every RAML-typed shape inside a JSON body, once per place it was written.

    Keyed by source position rather than by object: unwrap may give one
    declaration a copy per use, and those copies are one thing to its author.
    """
    seen: set[tuple[str, str]] = set()
    for iri, body in _json_bodies(ctx):
        assert body.shape is not None  # noqa: S101 - `_json_bodies` yields only typed bodies
        stack = [body.shape]
        while stack:
            base = stack.pop()
            if isinstance(base.shape, JsonShape):
                continue
            key = (base.location, str(base.key_pos))
            if key in seen:
                continue
            seen.add(key)
            yield iri, base
            stack.extend(_children(base))


def _name(base: BaseShape) -> str:
    return base.name or 'anonymous'


class IJsonTopLevel:
    meta: ClassVar = RuleMeta(
        'i-json-top-level',
        Category.I_JSON,
        'JSON bodies should be an object or an array',
        (
            'Software written to the older JSON specification accepts only an object or an array at the top '
            'level, so protocol designers SHOULD NOT use a top-level JSON text that is neither. A scalar body '
            'is valid JSON (RFC 8259) but not portable I-JSON; wrap it in an object.'
        ),
        Severity.WARNING,
        references=('RFC 7493 § 4.1',),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/json:\n            properties:\n              count: integer\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n          application/json: integer\n',
    )

    def payload(self, ctx: Context, iri: str, body: Body) -> Iterable[Finding]:
        base = body.shape
        if base is None or not _is_json(body.media_type) or not _is_scalar_top(base):
            return ()
        return (
            ctx.on(
                self.meta,
                'JSON body is neither an object nor an array',
                body,
                iri=iri,
                mediaType=body.media_type,
                type=base.type,
            ),
        )


def _is_scalar_top(base: BaseShape) -> bool:
    shape = base.shape
    if isinstance(shape, (ObjectShape, ArrayShape, AnyShape, JsonShape, NilShape)):
        return False
    if isinstance(shape, UnionShape):
        return any(_is_scalar_top(member) for member in shape.any_of or ())
    return True


class IJsonIntegerRange:
    meta: ClassVar = RuleMeta(
        'i-json-integer-range',
        Category.I_JSON,
        'JSON integers should stay within ±(2**53 - 1)',
        (
            'An I-JSON receiver cannot be expected to hold an integer beyond 9007199254740991 exactly, because '
            'many JSON implementations read every number as an IEEE 754 double. An `integer` with `format: '
            'int64` or `long`, or with a bound beyond that range, promises values some clients will round. '
            'Send such an identifier or amount as a string.'
        ),
        Severity.WARNING,
        references=('RFC 7493 § 2.2',),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n'
            '        properties:\n          id:\n            type: string\n            pattern: ^[0-9]+$\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n'
            '        properties:\n          id:\n            type: integer\n            format: int64\n'
        ),
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        for iri, base in _json_shapes(ctx):
            shape = base.shape
            if not isinstance(shape, IntegerShape):
                continue
            wide = shape.format is not None and shape.format.value in _WIDE_FORMATS
            bounds = [facet.value for facet in (shape.minimum, shape.maximum) if facet is not None]
            if wide or any(abs(bound) > _EXACT for bound in bounds):
                yield ctx.on(
                    self.meta,
                    'JSON integer may exceed the exact double range',
                    base,
                    iri=iri,
                    type=_name(base),
                    format=shape.format.value if shape.format is not None else 'none',
                )


class IJsonDateTime:
    meta: ClassVar = RuleMeta(
        'i-json-datetime',
        Category.I_JSON,
        'JSON timestamps should be RFC 3339 date-times with an offset',
        (
            'I-JSON recommends that timestamps be RFC 3339 strings. A `datetime` with `format: rfc2616` is an '
            'HTTP date, not RFC 3339, and a `datetime-only` has no UTC offset, which RFC 3339 calls unacceptable '
            'for the Internet because it is read differently in almost every time zone. Use `datetime`.'
        ),
        Severity.WARNING,
        references=('RFC 7493 § 4.3', 'RFC 3339 § 4.4'),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n'
            '        properties:\n          at: datetime\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n'
            '        properties:\n          at: datetime-only\n'
        ),
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        for iri, base in _json_shapes(ctx):
            shape = base.shape
            if isinstance(shape, DateTimeOnlyShape):
                reason = 'no UTC offset'
            elif isinstance(shape, DateTimeShape) and shape.format is not None and shape.format.value == 'rfc2616':
                reason = 'HTTP date rather than RFC 3339'
            else:
                continue
            yield ctx.on(
                self.meta,
                'JSON timestamp is not an RFC 3339 date-time with an offset',
                base,
                iri=iri,
                type=_name(base),
                reason=reason,
            )


class IJsonBinary:
    meta: ClassVar = RuleMeta(
        'i-json-binary',
        Category.I_JSON,
        'binary data in JSON should be base64url',
        (
            'I-JSON recommends base64url for binary data in a string. RAML 1.0 § File represents a `file` in '
            'JSON as base64, whose `+` and `/` differ from base64url, so a `file` inside an I-JSON body declares '
            'the other alphabet. Declare a `string` with a base64url `pattern` instead.'
        ),
        Severity.INFO,
        references=('RFC 7493 § 4.4', 'RFC 4648 § 5', 'RAML 1.0 § File'),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n'
            '        properties:\n          data:\n            type: string\n            pattern: ^[A-Za-z0-9_-]*$\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n'
            '        properties:\n          data: file\n'
        ),
    )

    def run(self, ctx: Context) -> Iterable[Finding]:
        for iri, base in _json_shapes(ctx):
            if isinstance(base.shape, FileShape):
                yield ctx.on(self.meta, 'JSON body carries a base64 file value', base, iri=iri, type=_name(base))
