"""RFC 9457 problem details, for APIs that adopt the format.

RFC 9457 obsoletes RFC 7807 and keeps its media types, so a document written
against either is judged the same way. Using problem details at all is the
adopter's choice, which is why this is an opt-in set; once adopted, the RFC
fixes the media type, the members' JSON types and what `status` may say.

Only RAML-typed bodies are read. A body typed by an included JSON Schema is
left alone rather than half-checked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.types.complex_ import ObjectShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import AnyShape, IntegerShape, NilShape, NumberShape, StringShape
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from fastraml.parser.endpoints import Response
    from fastraml.types.base import BaseShape
    from fastraml.views.lint.engine import Context

__all__ = ['ProblemMediaType', 'ProblemMemberTypes', 'ProblemStatus']

_PROBLEM_JSON: Final = 'application/problem+json'
_PROBLEM_TYPES: Final = frozenset({_PROBLEM_JSON, 'application/problem+xml'})
_FIRST_ERROR: Final = 400

#: Each standard member's JSON type (RFC 9457 § 3.1): the shapes that spell it.
_MEMBERS: Final[dict[str, tuple[str, tuple[type, ...]]]] = {
    'type': ('string', (StringShape,)),
    'status': ('number', (IntegerShape, NumberShape)),
    'title': ('string', (StringShape,)),
    'detail': ('string', (StringShape,)),
    'instance': ('string', (StringShape,)),
}


def _media_type(value: str) -> str:
    return value.partition(';')[0].strip().casefold()


def _problem_bodies(response: Response) -> Iterator[BaseShape]:
    """The RAML-typed `application/problem+json` body shapes of one response."""
    for media_type, body in response.bodies.items():
        if _media_type(media_type) != _PROBLEM_JSON or body.shape is None:
            continue
        if isinstance(body.shape.shape, (JsonShape, AnyShape)):
            continue
        yield body.shape


def _spells(base: BaseShape, kinds: tuple[type, ...]) -> bool:
    """Whether every value of `base` has the member's JSON type; `null` is tolerated."""
    shape = base.shape
    if isinstance(shape, UnionShape):
        return all(isinstance(member.shape, NilShape) or _spells(member, kinds) for member in shape.any_of or ())
    return isinstance(shape, kinds)


def _problem(status: str, members: str) -> str:
    return (
        f'#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      {status}:\n'
        f'        body:\n          application/problem+json:\n            properties:\n{members}'
    )


class ProblemMediaType:
    meta: ClassVar = RuleMeta(
        'problem-media-type',
        Category.PROBLEM_DETAILS,
        'error responses should use a problem details media type',
        (
            'RFC 9457 gives HTTP APIs one error format, so that a client needs no error parser of its own for '
            'each API. Its JSON form is `application/problem+json` and its XML form `application/problem+xml`; '
            'an error body under any other media type is not recognisable as problem details. This set is for '
            'APIs that have adopted the format.'
        ),
        Severity.WARNING,
        references=('RFC 9457 § 3', 'RFC 9457 Appendix B'),
        good=_problem('404', '              title: string\n'),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      404:\n'
            '        body:\n          application/json: string\n'
        ),
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        if int(response.code) < _FIRST_ERROR or not response.bodies:
            return ()
        if any(_media_type(media_type) in _PROBLEM_TYPES for media_type in response.bodies):
            return ()
        return (
            ctx.on(
                self.meta,
                'error response has no problem details body',
                response,
                iri=iri,
                status=response.code,
                mediaTypes=','.join(response.bodies),
            ),
        )


class ProblemMemberTypes:
    meta: ClassVar = RuleMeta(
        'problem-member-types',
        Category.PROBLEM_DETAILS,
        'problem details members should have their standard types',
        (
            'A problem details object is a JSON object whose standard members have fixed types: `type` and '
            '`instance` are URI-reference strings, `title` and `detail` are strings, and `status` is a number. '
            'A consumer MUST ignore a member whose value has another type, so a member declared with the wrong '
            'type is one no conforming client will read. Extension members are unconstrained.'
        ),
        Severity.WARNING,
        references=('RFC 9457 § 3', 'RFC 9457 § 3.1'),
        good=_problem('404', '              status: integer\n              title: string\n'),
        bad=_problem('404', '              status: string\n              title: string\n'),
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        found = []
        for base in _problem_bodies(response):
            shape = base.shape
            if not isinstance(shape, ObjectShape):
                found.append(
                    ctx.on(
                        self.meta,
                        'problem details body is not an object',
                        base,
                        iri=iri,
                        status=response.code,
                        type=base.type,
                    )
                )
                continue
            properties = shape.properties or {}
            for member, (json_type, kinds) in _MEMBERS.items():
                prop = properties.get(member)
                if prop is None or _spells(prop.base, kinds):
                    continue
                found.append(
                    ctx.on(
                        self.meta,
                        'problem details member has the wrong type',
                        prop.base,
                        iri=iri,
                        status=response.code,
                        member=member,
                        expected=json_type,
                        type=prop.base.type,
                    )
                )
        return found


class ProblemStatus:
    meta: ClassVar = RuleMeta(
        'problem-status',
        Category.PROBLEM_DETAILS,
        "a problem's status should admit its response's code",
        (
            '`status` is advisory, but a generator MUST put the same code in the actual HTTP response, so that '
            'HTTP software that never reads the body still behaves correctly. A `status` whose enumeration '
            "excludes the response's own code describes a body that response cannot carry."
        ),
        Severity.WARNING,
        references=('RFC 9457 § 3.1.2',),
        good=_problem('404', '              status:\n                type: integer\n                enum: [404]\n'),
        bad=_problem('404', '              status:\n                type: integer\n                enum: [400]\n'),
    )

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        found = []
        code = int(response.code)
        for base in _problem_bodies(response):
            shape = base.shape
            prop = (shape.properties or {}).get('status') if isinstance(shape, ObjectShape) else None
            if prop is None or not prop.base.enum:
                continue
            allowed = [member.raw for member in prop.base.enum]
            if code in allowed:
                continue
            found.append(
                ctx.on(
                    self.meta,
                    "problem details status excludes the response's code",
                    prop.base,
                    iri=iri,
                    status=response.code,
                    allowed=','.join(str(value) for value in allowed),
                )
            )
        return found
