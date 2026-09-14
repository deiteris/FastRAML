from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, cast

import simplejson as json
from aiohttp.multipart import BodyPartReader
from fastraml import (
    AnyShape,
    ArrayShape,
    BooleanShape,
    FileShape,
    IntegerShape,
    NilShape,
    NumberShape,
    ObjectShape,
    StringShape,
    UnionShape,
)

from raml_mock.errors import RequestIssue, RequestValidationError
from raml_mock.media import base_media_type, is_json_media_type
from raml_mock.shapes import concrete_shape, file_type_accepts
from raml_mock.values import detach

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from aiohttp import web
    from fastraml import BaseShape, Body, Parameter, Request

    from raml_mock.codecs import BodyCodec

__all__ = ['DecodedRequest', 'decode_request']

_FORM = 'application/x-www-form-urlencoded'
_MULTIPART = 'multipart/form-data'
type WireValue = str | bytes | bytearray | memoryview


@dataclass(slots=True, frozen=True)
class DecodedRequest:
    """Transport values decoded and validated against one RAML operation."""

    path: dict[str, object]
    query: dict[str, object]
    headers: dict[str, object]
    body: object = None
    media_type: str | None = None


async def decode_request(
    request: web.Request,
    declared: Request | None,
    uri_parameters: Mapping[str, Parameter],
    path_values: Mapping[str, str],
    codecs: Mapping[str, BodyCodec],
) -> DecodedRequest:
    issues: list[RequestIssue] = []
    path = _parameters(uri_parameters, {name: [value] for name, value in path_values.items()}, 'path', issues)
    if declared is None:
        if issues:
            raise RequestValidationError(400, issues)
        return DecodedRequest(path, {}, {})

    query_values = {name: request.query.getall(name) for name in request.query}
    header_values = {name: request.headers.getall(name, []) for name in declared.headers}
    query = _parameters(declared.query_parameters, query_values, 'query', issues)
    headers = _parameters(declared.headers, header_values, 'header', issues)
    if declared.query_string is not None:
        query = _coerce_mapping(declared.query_string, query_values, 'query', issues)

    body: object = None
    media_type: str | None = None
    if declared.bodies:
        if not request.can_read_body:
            issues.append(RequestIssue('body', 'request body is required'))
        else:
            media_type = base_media_type(request.content_type)
            selected = _body_for(declared.bodies, media_type)
            if selected is None or selected.shape is None:
                raise RequestValidationError(
                    415,
                    [RequestIssue('body', 'unsupported content type', {'contentType': media_type})],
                )
            body = await _decode_body(request, selected, media_type, issues, codecs)
    elif request.can_read_body:
        issues.append(RequestIssue('body', 'operation declares no request body'))

    if issues:
        raise RequestValidationError(400, issues)
    return DecodedRequest(path, query, headers, body, media_type)


def _parameters(
    declared: Mapping[str, Parameter],
    supplied: Mapping[str, Sequence[WireValue]],
    location: str,
    issues: list[RequestIssue],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, parameter in declared.items():
        values = supplied.get(name, ())
        if not values:
            if location != 'path' and parameter.base.default is not None:
                result[name] = _apply_defaults(parameter.base, detach(parameter.base.default.raw))
                continue
            if parameter.required:
                issues.append(RequestIssue(f'{location}.{name}', 'required parameter is missing'))
            continue
        try:
            value = _coerce_values(parameter.base, values)
        except (ValueError, TypeError) as error:
            issues.append(RequestIssue(f'{location}.{name}', 'parameter cannot be decoded', str(error)))
            continue
        if not _valid(parameter.base, value, f'{location}.{name}', 'parameter does not match its RAML type', issues):
            continue
        result[name] = value
    return result


def _coerce_values(base: BaseShape, values: Sequence[WireValue]) -> object:
    shape = concrete_shape(base)
    if isinstance(shape, ArrayShape):
        if shape.items is None:
            return list(values)
        return [_coerce_one(shape.items, value) for value in values]
    return _coerce_one(base, values[-1])


def _coerce_one(base: BaseShape, value: WireValue) -> object:
    shape = concrete_shape(base)
    if not isinstance(value, str):
        value = bytes(value)
        if isinstance(shape, (FileShape, AnyShape)):
            return value
        value = value.decode('utf-8')
    return _coerce_text(shape, value)


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f'{text!r} is not a number') from error


def _boolean(text: str) -> bool:
    lowered = text.lower()
    if lowered not in {'true', 'false'}:
        raise ValueError(f'{text!r} is not true or false')
    return lowered == 'true'


def _nil(text: str) -> None:
    if text.lower() not in {'null', '~'}:
        raise ValueError(f'{text!r} is not null')


#: How one kind reads its HTTP text. A table rather than an `isinstance` chain,
#: which had to be split across two functions to stay under a complexity limit
#: -- and the split put `boolean` and `nil` somewhere no reader would look for
#: them. A kind absent here keeps the text as written, which is what `any` and
#: every date kind want.
_FROM_TEXT: dict[type[object], Callable[[str], object]] = {
    FileShape: str.encode,
    IntegerShape: lambda text: int(text, 10),
    NumberShape: _decimal,
    BooleanShape: _boolean,
    NilShape: _nil,
}


def _coerce_text(shape: object | None, text: str) -> object:
    if isinstance(shape, UnionShape):
        return _coerce_union(shape, text)
    reader = _FROM_TEXT.get(type(shape))
    return text if reader is None else reader(text)


def _coerce_union(shape: UnionShape, text: str) -> object:
    for member in shape.any_of or ():
        try:
            candidate = _coerce_one(member, text)
        except (ValueError, TypeError, UnicodeDecodeError):
            continue
        if member.validate(candidate) is None:
            return candidate
    return text


def _coerce_mapping(
    base: BaseShape,
    supplied: Mapping[str, Sequence[WireValue]],
    location: str,
    issues: list[RequestIssue],
) -> dict[str, object]:
    shape = concrete_shape(base)
    if not isinstance(shape, ObjectShape):
        issues.append(RequestIssue(location, 'structured form/query data requires an object type'))
        return {}
    result: dict[str, object] = {}
    properties = shape.properties or {}
    for name, values in supplied.items():
        prop = properties.get(name)
        if prop is None:
            result[name] = values[-1]
            continue
        try:
            result[name] = _coerce_values(prop.base, values)
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            issues.append(RequestIssue(f'{location}.{name}', 'field cannot be decoded', str(error)))
    defaulted = cast('dict[str, object]', _apply_defaults(base, result))
    _valid(base, defaulted, location, 'value does not match its RAML type', issues)
    return defaulted


def _apply_defaults(base: BaseShape, value: object) -> object:
    shape = concrete_shape(base)
    if isinstance(shape, ObjectShape) and isinstance(value, dict):
        supplied = {
            name: _apply_defaults((shape.properties or {})[name].base, item)
            if name in (shape.properties or {})
            else item
            for name, item in cast('dict[str, object]', value).items()
        }
        result = dict(supplied)
        for name, prop in (shape.properties or {}).items():
            if name not in result and prop.base.default is not None:
                result[name] = _apply_defaults(prop.base, detach(prop.base.default.raw))
        return supplied if base.validate(supplied) is None and base.validate(result) is not None else result
    if isinstance(shape, ArrayShape) and isinstance(value, list) and shape.items is not None:
        return [_apply_defaults(shape.items, item) for item in value]
    return value


async def _decode_body(
    request: web.Request,
    body: Body,
    media_type: str,
    issues: list[RequestIssue],
    codecs: Mapping[str, BodyCodec],
) -> object:
    assert body.shape is not None  # noqa: S101 - selected above
    file_shape = concrete_shape(body.shape)
    if isinstance(file_shape, FileShape) and not file_type_accepts(file_shape, media_type):
        raise RequestValidationError(
            415,
            [RequestIssue('body', 'content type is not allowed by fileTypes', {'contentType': media_type})],
        )
    try:
        custom = codecs.get(media_type)
        if custom is not None:
            value = await custom.decode(request)
        elif is_json_media_type(media_type):
            # `use_decimal` to match the encoder, and for the same reason the
            # parser gives: a number that goes through `float` comes back
            # rounded, so a body echoed to the client is not the body it sent.
            # Query and header numbers already arrive as `Decimal` from
            # `_coerce_number`; this is the path that did not.
            value = json.loads(
                await request.text(),
                parse_constant=_reject_json_constant,
                use_decimal=True,
            )
        elif media_type == _FORM:
            return await _decode_form(request, body.shape, issues)
        elif media_type == _MULTIPART:
            return await _decode_multipart(request, body.shape, issues)
        else:
            value = await _decode_scalar_body(request, body.shape, media_type)
    except json.JSONDecodeError as error:
        issues.append(RequestIssue('body', 'body is not valid JSON', {'line': error.lineno, 'column': error.colno}))
        return None
    except UnicodeDecodeError as error:
        issues.append(RequestIssue('body', 'body is not valid text', str(error)))
        return None

    value = _apply_defaults(body.shape, value)
    _valid(body.shape, value, 'body', 'body does not match its RAML type', issues)
    return value


async def _decode_form(request: web.Request, base: BaseShape, issues: list[RequestIssue]) -> dict[str, object]:
    posted = await request.post()
    values: dict[str, list[WireValue]] = {
        name: [item for item in posted.getall(name) if isinstance(item, (str, bytes))] for name in posted
    }
    return _coerce_mapping(base, values, 'body', issues)


async def _decode_multipart(request: web.Request, base: BaseShape, issues: list[RequestIssue]) -> dict[str, object]:
    values: dict[str, list[WireValue]] = {}
    reader = await request.multipart()
    while part := await reader.next():
        if not isinstance(part, BodyPartReader) or part.name is None:
            continue
        _check_part_media_type(base, part, issues)
        item: WireValue = bytes(await part.read()) if part.filename is not None else await part.text()
        values.setdefault(part.name, []).append(item)
    return _coerce_mapping(base, values, 'body', issues)


async def _decode_scalar_body(request: web.Request, base: BaseShape, media_type: str) -> object:
    shape = concrete_shape(base)
    if media_type in {'application/xml', 'text/xml'}:
        if not isinstance(shape, (AnyShape, FileShape, StringShape)):
            raise _unsupported_body(media_type, 'structured XML has no RAML codec')
        return _coerce_one(base, await request.text())
    if media_type.startswith('text/'):
        return _coerce_one(base, await request.text())
    if not isinstance(shape, (AnyShape, FileShape)):
        raise _unsupported_body(media_type, 'no decoder for structured content type')
    return await request.read()


def _unsupported_body(media_type: str, message: str) -> RequestValidationError:
    return RequestValidationError(415, [RequestIssue('body', message, {'contentType': media_type})])


def _body_for(bodies: Mapping[str, Body], media_type: str) -> Body | None:
    return next((body for declared, body in bodies.items() if base_media_type(declared) == media_type), None)


def _check_part_media_type(base: BaseShape, part: BodyPartReader, issues: list[RequestIssue]) -> None:
    shape = concrete_shape(base)
    if not isinstance(shape, ObjectShape) or shape.properties is None or part.name is None:
        return
    prop = shape.properties.get(part.name)
    if prop is None:
        return
    file_shape = concrete_shape(prop.base)
    if not isinstance(file_shape, FileShape):
        return
    media_type = base_media_type(part.headers.get('Content-Type', 'application/octet-stream'))
    if not file_type_accepts(file_shape, media_type):
        issues.append(
            RequestIssue(
                f'body.{part.name}',
                'content type is not allowed by fileTypes',
                {'contentType': media_type},
            )
        )


def _reject_json_constant(value: str) -> object:
    raise json.JSONDecodeError(f'{value} is not valid JSON', value, 0)


def _valid(
    base: BaseShape,
    value: object,
    location: str,
    message: str,
    issues: list[RequestIssue],
) -> bool:
    error = base.validate(value)
    if error is None:
        return True
    issues.append(RequestIssue(location, message, error.to_dict()))
    return False
