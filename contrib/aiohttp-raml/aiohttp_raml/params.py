"""Where a handler's parameters come from.

Two ways of saying it, and the first is usually enough.

**By signature position**, which is the convention an aiohttp-pydantic user
already knows:

| Position | RAML node |
|---|---|
| positional-only | `uriParameters` |
| a `BaseModel` | `body` |
| positional-or-keyword | `queryParameters` |
| keyword-only | `headers` |

**By marker**, where the position cannot say it: `Annotated[str, Header('X-Trace')]`.
A marker always wins.

Markers say *where* a value comes from; `Field` says what it must look like. The
two compose -- `Annotated[str, Header('X-Trace'), Field(max_length=64)]` -- and
neither has to know about the other.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Final, get_args

from pydantic import BaseModel

from aiohttp_raml.multipart import File, is_upload

__all__ = ['Body', 'Declared', 'Header', 'QueryParam', 'UriParam', 'read_signature', 'wire_name']

#: The absence of a default, where `None` is a default a parameter can have.
MISSING: Final = object()

#: The RAML node each marker names.
URI: Final = 'uri'
QUERY: Final = 'query'
HEADER: Final = 'header'
BODY: Final = 'body'


@dataclass(frozen=True, slots=True)
class Place:
    """A marker: the RAML node a parameter belongs to, and its name on the wire."""

    #: Set by each subclass. There is one marker per node and no others.
    node: ClassVar[str]

    name: str | None = None


@dataclass(frozen=True, slots=True)
class UriParam(Place):
    node: ClassVar[str] = URI


@dataclass(frozen=True, slots=True)
class QueryParam(Place):
    node: ClassVar[str] = QUERY


@dataclass(frozen=True, slots=True)
class Header(Place):
    node: ClassVar[str] = HEADER


@dataclass(frozen=True, slots=True)
class Body(Place):
    node: ClassVar[str] = BODY

    media: str = 'application/json'


@dataclass(slots=True)
class Declared:
    """One parameter, as both halves of this package read it.

    The injector needs `place`, `wire` and `positional`; the renderer needs
    `place`, `wire`, `annotation` and `default`. One record, read twice, so the
    document cannot describe a parameter the injector takes from somewhere else.
    """

    name: str
    wire: str
    place: str
    annotation: Any
    default: Any = MISSING
    positional: bool = False
    media: str = 'application/json'
    #: The RAML `file` facets, when this parameter is an upload.
    file: File | None = None

    @property
    def is_file(self) -> bool:
        return is_upload(self.annotation)

    @property
    def required(self) -> bool:
        return self.default is MISSING


def wire_name(identifier: str) -> str:
    """`x_request_id` -> `X-Request-Id`.

    A Python parameter cannot be called `X-Request-Id`, and a header's name on
    the wire is what a client has to send. `Header('X-Trace')` overrides this
    where the guess is wrong.
    """
    return '-'.join(part.title() for part in identifier.split('_'))


def _marker(annotation: Any) -> tuple[Any, Place | None, File | None]:
    """Take this package's markers out of an `Annotated`, and return the rest.

    They are removed rather than left in place: what remains is handed to the
    type walk, which reads `Annotated` metadata as facets and would report
    `Header('X-Trace')` as one it has no RAML spelling for. `Field(...)` and
    anything else stays, because it is exactly that.
    """
    if not hasattr(annotation, '__metadata__'):
        return annotation, None, None
    base, *metadata = get_args(annotation)
    place = next((item for item in metadata if isinstance(item, Place)), None)
    facets = next((item for item in metadata if isinstance(item, File)), None)
    if place is None and facets is None:
        return annotation, None, None
    rest = tuple(item for item in metadata if not isinstance(item, (Place, File)))
    return (Annotated[(base, *rest)] if rest else base), place, facets


def _inferred(annotation: Any, kind: inspect._ParameterKind) -> str:
    """The node a parameter belongs to when nothing marks it."""
    if kind is inspect.Parameter.POSITIONAL_ONLY:
        return URI
    if kind is inspect.Parameter.KEYWORD_ONLY:
        return HEADER
    if _is_model(annotation) or is_upload(annotation):
        return BODY
    return QUERY


def _is_model(annotation: Any) -> bool:
    inner = annotation
    if hasattr(annotation, '__metadata__'):
        inner = get_args(annotation)[0]
    return isinstance(inner, type) and issubclass(inner, BaseModel)


def read_signature(handler: Any, hints: dict[str, Any], ignore: tuple[str, ...]) -> list[Declared]:
    """A handler's parameters, in declaration order.

    `hints` is the caller's `get_type_hints(..., include_extras=True)`, passed in
    so a handler is resolved once rather than once per reader.
    """
    out: list[Declared] = []
    for name, parameter in inspect.signature(handler).parameters.items():
        if name in ignore:
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise TypeError(f'{_named(handler)}: *args and **kwargs cannot be described')
        if name not in hints:
            raise TypeError(f'{_named(handler)}: parameter {name!r} has no annotation')
        annotation, marker, facets = _marker(hints[name])
        place = marker.node if marker is not None else _inferred(annotation, parameter.kind)
        wire = (marker.name if marker is not None and marker.name else None) or (
            wire_name(name) if place == HEADER else name
        )
        out.append(
            Declared(
                name=name,
                wire=wire,
                place=place,
                annotation=annotation,
                default=MISSING if parameter.default is parameter.empty else parameter.default,
                positional=parameter.kind is parameter.POSITIONAL_ONLY,
                media=marker.media if isinstance(marker, Body) else 'application/json',
                file=facets,
            )
        )
    _check_one_body(handler, out)
    return out


def _check_one_body(handler: Any, declared: list[Declared]) -> None:
    """One body -- unless it is multipart, where each part is a field of it.

    `multipart/form-data` *is* one body; the several parameters are its fields.
    Without an upload among them there is nothing to make a form out of, and two
    JSON bodies is a handler that cannot be served.

    In a multipart body every non-file field must be declared **before** the
    files. The parts are read from a stream in declaration order, and a field's
    value has to be validated before the handler runs, so a field after a file
    would have to be read past an upload nothing has consumed yet.
    """
    bodies = [item for item in declared if item.place == BODY]
    if len(bodies) < 2:  # noqa: PLR2004 - zero or one body needs no rule
        return
    if not any(item.is_file for item in bodies):
        names = [item.name for item in bodies]
        raise TypeError(f'{_named(handler)}: {names} are all the request body, and a request has one')
    seen_file = False
    for item in bodies:
        if item.is_file:
            seen_file = True
        elif seen_file:
            raise TypeError(
                f'{_named(handler)}: {item.name!r} is a form field declared after a file; '
                f'fields must come before uploads so they can be read without buffering one'
            )


def _named(handler: Any) -> str:
    return getattr(handler, '__qualname__', repr(handler))
