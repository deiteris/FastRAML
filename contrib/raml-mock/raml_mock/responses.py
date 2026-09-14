from __future__ import annotations

import base64
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

import simplejson as json
from aiohttp import MultipartWriter, web
from fastraml import AnyShape, FileShape, ObjectShape, StringShape

from raml_mock.config import GenerationOptions, RouteBehavior
from raml_mock.errors import MockGenerationError, RequestIssue, RequestValidationError
from raml_mock.generate import generate
from raml_mock.media import base_media_type, is_json_media_type
from raml_mock.shapes import concrete_shape, file_type_accepts, shape_name
from raml_mock.status import is_success

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml import BaseShape, Body, Operation, Response

    from raml_mock.codecs import BodyCodec

__all__ = ['build_response']

_GENERATED = object()
#: Media types that carry a *stream* rather than a representation. RAML
#: describes one representation of one entity and says nothing about how many
#: events a stream holds or how they are paced, so there is nothing in a
#: document for the mock to honour. Answering with a single frame would be a
#: stream in name only, so these are refused like structured XML is.
_STREAMED = frozenset({'application/x-ndjson', 'text/event-stream'})


@dataclass(slots=True, frozen=True)
class ResponseContext:
    behavior: RouteBehavior = field(default_factory=RouteBehavior)
    generation: GenerationOptions = field(default_factory=GenerationOptions)
    key: str = ''
    value: object = _GENERATED


@dataclass(slots=True, frozen=True)
class _Accept:
    media_type: str
    quality: float


@dataclass(slots=True, frozen=True)
class _SelectedResponse:
    response: Response
    status: int


def build_response(
    request: web.Request,
    operation: Operation,
    codecs: Mapping[str, BodyCodec],
    context: ResponseContext | None = None,
) -> web.Response:
    """The declared response for *request*, as a complete body.

    Synchronous on purpose. Nothing here suspends, so a caller that decides
    something from the result -- committing a stateful write, say -- runs
    without the event loop interleaving another request in between.
    """
    selected_context = context or ResponseContext()
    behavior = selected_context.behavior
    selected_response = _select_response(request, operation, behavior.status)
    response = selected_response.response
    status = selected_response.status
    headers = _response_headers(response)
    if not response.bodies or status in {204, 304}:
        return web.Response(status=status, headers=headers)

    selected = _select_body(request.headers.get('Accept'), response.bodies, codecs)
    if selected is None or selected.shape is None:
        raise RequestValidationError(406, [RequestIssue('header.Accept', 'no acceptable response representation')])
    example = request.headers.get('X-RAML-Mock-Example') or behavior.example
    value = selected_context.value
    if value is _GENERATED:
        value = generate(
            selected.shape,
            example=example,
            options=selected_context.generation,
            key=f'{selected_context.key}:{status}:{selected.media_type}',
        )
    elif selected.shape.validate(value) is not None:
        raise MockGenerationError(f'configured value does not satisfy {shape_name(selected.shape)}')
    # HEAD is built exactly like GET. aiohttp drops the payload and keeps
    # `Content-Length` and `Content-Type`, which is what RFC 9110 section 9.3.2
    # asks for -- returning early here instead sent neither.
    return _encode(status, headers, selected, value, codecs)


def _select_response(request: web.Request, operation: Operation, configured: str | None) -> _SelectedResponse:
    if not operation.responses:
        raise RequestValidationError(500, [RequestIssue('response', 'operation declares no response')])
    requested = request.headers.get('X-RAML-Mock-Status') or configured
    if requested is not None:
        selected = operation.responses.get(requested)
        if selected is None:
            location = 'header.X-RAML-Mock-Status' if 'X-RAML-Mock-Status' in request.headers else 'response'
            raise RequestValidationError(
                400,
                [RequestIssue(location, 'status is not declared', {'status': requested})],
            )
        return _SelectedResponse(selected, _status_of(requested))
    response = next(
        (item for code, item in operation.responses.items() if is_success(_status_of(code))),
        next(iter(operation.responses.values())),
    )
    return _SelectedResponse(response, _status_of(response.code))


def _status_of(code: str) -> int:
    # Every `responses:` key fastRAML hands over is a 3-digit status -- it
    # rejects anything else, `4xx` classes included (docs/08 section 3). The
    # guard is for an `Operation` assembled in Python, not for a parsed one.
    if not code.isdigit():
        raise RequestValidationError(
            500,
            [RequestIssue('response', 'response status is not a 3-digit code', {'status': code})],
        )
    return int(code)


def _response_headers(response: Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, parameter in response.headers.items():
        value = generate(parameter.base)
        headers[name] = _header_value(value)
    return headers


def _header_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return cast('str', json.dumps(value, separators=(',', ':')))
    return _text_value(value)


def _text_value(value: object) -> str:
    """One scalar as wire text.

    `str(True)` is `'True'`, which is Python's spelling of a boolean and not
    RAML's. Every encoder here has to agree on that, so they share this.
    """
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _select_body(accept: str | None, bodies: Mapping[str, Body], codecs: Mapping[str, BodyCodec]) -> Body | None:
    supported = [
        (media, body)
        for media, body in bodies.items()
        if body.shape is not None and _can_encode(media, body.shape, codecs)
    ]
    if not supported:
        return None
    if not accept:
        return supported[0][1]
    accepted = _parse_accept(accept)
    ranked: list[tuple[float, int, Body]] = []
    for body_order, (media, body) in enumerate(supported):
        quality = _quality(base_media_type(media), accepted)
        if quality > 0:
            ranked.append((-quality, body_order, body))
    return None if not ranked else min(ranked)[2]


def _parse_accept(value: str) -> list[_Accept]:
    result: list[_Accept] = []
    for raw in value.split(','):
        media, *parameters = raw.split(';')
        quality = 1.0
        for parameter in parameters:
            name, separator, text = parameter.strip().partition('=')
            if separator and name.lower() == 'q':
                try:
                    quality = float(text)
                except ValueError:
                    quality = 0.0
        result.append(_Accept(media.strip().lower(), quality))
    return result


def _matches(accepted: str, offered: str) -> bool:
    if accepted in ('*/*', offered):
        return True
    accepted_type, separator, accepted_subtype = accepted.partition('/')
    offered_type, _, _offered_subtype = offered.partition('/')
    return bool(separator and accepted_subtype == '*' and accepted_type == offered_type)


def _quality(offered: str, accepted: list[_Accept]) -> float:
    matches = [item for item in accepted if _matches(item.media_type, offered)]
    if not matches:
        return 0.0
    return max(matches, key=lambda item: _specificity(item.media_type)).quality


def _specificity(media_type: str) -> int:
    if media_type == '*/*':
        return 0
    return 1 if media_type.endswith('/*') else 2


def _can_encode(media_type: str, base: BaseShape, codecs: Mapping[str, BodyCodec]) -> bool:
    media = base_media_type(media_type)
    shape = concrete_shape(base)
    if media in _STREAMED or (isinstance(shape, FileShape) and not file_type_accepts(shape, media)):
        return False
    if media in codecs or is_json_media_type(media):
        return True
    if media in {'application/x-www-form-urlencoded', 'multipart/form-data'}:
        return isinstance(shape, ObjectShape)
    if media in {'application/xml', 'text/xml'}:
        return isinstance(shape, (AnyShape, FileShape, StringShape))
    if media.startswith('text/'):
        return not isinstance(shape, ObjectShape)
    return isinstance(shape, (AnyShape, FileShape))


def _encode(
    status: int,
    headers: dict[str, str],
    body: Body,
    value: object,
    codecs: Mapping[str, BodyCodec],
) -> web.Response:
    assert body.shape is not None  # noqa: S101 - selected by _select_body
    media = base_media_type(body.media_type)
    custom = codecs.get(media)
    if custom is not None:
        payload = custom.encode(value)
    elif is_json_media_type(media):
        payload = _json_payload(value, body.shape, media)
    elif media == 'application/x-www-form-urlencoded':
        payload = _form_payload(value, body.shape, media)
    elif media == 'multipart/form-data':
        if not isinstance(value, dict):
            raise _encoding_error(media, body.shape)
        return web.Response(
            status=status,
            headers=headers,
            body=_multipart(cast('dict[str, object]', value), body.shape),
        )
    else:
        payload = _scalar_payload(value, body.shape, media)
    return _payload_response(status, headers, media, payload)


def _payload_response(status: int, headers: dict[str, str], media_type: str, payload: str | bytes) -> web.Response:
    if isinstance(payload, str):
        return web.Response(status=status, headers=headers, text=payload, content_type=media_type)
    return web.Response(status=status, headers=headers, body=payload, content_type=media_type)


def _json_payload(value: object, base: BaseShape, media_type: str) -> str:
    # A value a handler or the state store supplied need not be JSON at all --
    # a `set` reaches here intact, because RAML validation accepts it as an
    # `any`. Without this the `TypeError` surfaced as a *state* failure.
    try:
        return cast(
            'str',
            json.dumps(value, separators=(',', ':'), ensure_ascii=False, use_decimal=True, allow_nan=False),
        )
    except (TypeError, ValueError) as error:
        raise MockGenerationError(f'value for {shape_name(base)} cannot be encoded as {media_type}') from error


def _form_payload(value: object, base: BaseShape, media_type: str) -> str:
    if not isinstance(value, dict):
        raise _encoding_error(media_type, base)
    fields = {name: _form_value(item) for name, item in cast('dict[str, object]', value).items()}
    return urlencode(fields, doseq=True)


def _scalar_payload(value: object, base: BaseShape, media_type: str) -> str | bytes:
    if isinstance(concrete_shape(base), FileShape) and isinstance(value, str):
        return base64.b64decode(value)
    if isinstance(value, bytes):
        return value
    if value is None:
        return ''
    # `Decimal` belongs here: it is what generation produces for any `number`
    # that is not integral, so without it `type: number` over `text/plain`
    # answered 500 for every value with a fractional part.
    if isinstance(value, (str, int, float, bool, Decimal)):
        return _text_value(value)
    raise _encoding_error(media_type, base)


def _multipart(value: dict[str, object], base: BaseShape) -> MultipartWriter:
    writer = MultipartWriter('form-data')
    shape = concrete_shape(base)
    properties = shape.properties if isinstance(shape, ObjectShape) and shape.properties is not None else {}
    for name, raw in value.items():
        if '\r' in name or '\n' in name:
            raise MockGenerationError('multipart field name contains CR or LF')
        values = raw if isinstance(raw, list) else [raw]
        prop = properties.get(name)
        field_shape = None if prop is None else concrete_shape(prop.base)
        for item in values:
            payload = item
            if isinstance(field_shape, FileShape) and isinstance(payload, str):
                payload = base64.b64decode(payload)
            encoded = payload if isinstance(payload, bytes) else _text_value(payload)
            part = writer.append(encoded, {'Content-Type': _part_media_type(field_shape)})
            if isinstance(field_shape, FileShape):
                part.set_content_disposition('form-data', name=name, filename=name)
            else:
                part.set_content_disposition('form-data', name=name)
    return writer


def _part_media_type(shape: object | None) -> str:
    if not isinstance(shape, FileShape):
        return 'text/plain; charset=utf-8'
    if not shape.file_types:
        return 'application/octet-stream'
    selected = shape.file_types[0].value
    return 'application/octet-stream' if selected == '*/*' else selected


def _form_value(value: object) -> object:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return [_form_value(item) for item in value]
    return value


def _encoding_error(media_type: str, base: BaseShape) -> RequestValidationError:
    return RequestValidationError(
        500,
        [
            RequestIssue(
                'response',
                'generated value cannot be encoded',
                {'contentType': media_type, 'shape': shape_name(base)},
            )
        ],
    )
