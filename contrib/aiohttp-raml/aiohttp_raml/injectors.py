"""Validate a request against a handler's declarations, and call it with the result.

One pydantic model per RAML node -- `uriParameters`, `queryParameters`,
`headers` -- built once when the handler is decorated, plus the body's own
model. A request that does not validate becomes a 400 naming the node it failed
in, so a client is told *where* it was wrong.

The same `Declared` records drive `render.py`, so the document cannot describe a
parameter the injector takes from somewhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, get_args, get_origin

from aiohttp import web
from pydantic import BaseModel, TypeAdapter, ValidationError, create_model

from aiohttp_raml.errors import STATUS, RequestError, describe
from aiohttp_raml.multipart import File, PartRejected, Parts, UploadedFile
from aiohttp_raml.params import BODY, HEADER, QUERY, URI, Declared
from aiohttp_raml.responses import Responds

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['Bound', 'bind', 'error_response']

#: The RAML node a failure happened in, as it is reported to the client.
_NODE_NAMES = {URI: 'uriParameters', QUERY: 'queryParameters', HEADER: 'headers', BODY: 'body'}


def error_response(error: ValidationError, node: str) -> web.Response:
    """A validation failure, as JSON, naming the node it happened in.

    Exactly `RequestError`'s four fields. pydantic also offers `input` and
    `ctx`; neither is written, because `input` echoes request data back and
    `ctx` varies by constraint so no one type describes it.
    """
    where = _NODE_NAMES.get(node, node)
    return _failures(
        [
            describe(where, str(detail['msg']), kind=str(detail['type']), loc=list(detail['loc']))
            for detail in error.errors(include_url=False)
        ]
    )


def _failures(details: list[dict[str, object]]) -> web.Response:
    return web.json_response(details, status=STATUS, dumps=_dumps)


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _model(name: str, fields: Sequence[Declared]) -> type[BaseModel]:
    """One pydantic model over the parameters of a single node.

    Keyed by the *handler's* parameter name, not the wire name: `X-Request-Id`
    is not an identifier, and a model whose fields are not identifiers only
    works by way of pydantic storing them in `__dict__`. `_collect` does the
    wire-to-name mapping before anything reaches here.
    """
    spec: dict[str, Any] = {item.name: (item.annotation, ... if item.required else item.default) for item in fields}
    return create_model(name, **spec)


@dataclass(slots=True)
class Bound:
    """One handler's declarations, compiled to what the request needs."""

    declared: list[Declared]
    models: dict[str, type[BaseModel]]
    body: Declared | None
    body_adapter: TypeAdapter[Any] | None
    #: The fields of a `multipart/form-data` body, in declaration order.
    parts: list[Declared]
    #: What the handler declared, in declaration order, for the renderer.
    declared_responses: list[Responds]
    #: The same, compiled, for `check`.
    responses: dict[int, TypeAdapter[Any] | None]

    async def call(self, handler: Any, request: web.Request, first: Any = None) -> tuple[bool, Any]:
        """Validate, then call.

        Returns `(ran, response)`. `ran` is False when validation refused the
        request, and the response is this package's 400 rather than the
        handler's -- which is why `check` must not be given it: a handler is not
        obliged to declare a failure it never produced.
        """
        args: list[Any] = [] if first is None else [first]
        kwargs: dict[str, Any] = {}
        for node in (URI, QUERY, HEADER):
            model = self.models.get(node)
            if model is None:
                continue
            try:
                validated = model(**_collect(node, request, self.declared))
            except ValidationError as error:
                return False, error_response(error, node)
            _place(self.declared, node, validated, args, kwargs)

        if self.parts:
            refused = await _multipart(request, self.parts, kwargs)
            if refused is not None:
                return False, refused
        elif self.body is not None and self.body_adapter is not None:
            try:
                payload = await request.json()
            except JSONDecodeError:
                return False, _failures([describe('body', 'the body is not valid JSON', kind='json_invalid')])
            try:
                kwargs[self.body.name] = self.body_adapter.validate_python(payload)
            except ValidationError as error:
                return False, error_response(error, BODY)

        return True, await handler(*args, **kwargs)

    def check(self, response: Any, handler: Any) -> None:
        """Assert a response matches what the handler declared.

        Off unless `validate(check_responses=True)`. Nothing static can do this
        -- `web.json_response` takes `Any` -- so this is the only thing standing
        between a document that claims `404 -> Error` and a handler that returns
        something else.
        """
        body = getattr(response, 'body', None)
        if not isinstance(response, web.Response) or not isinstance(body, (bytes, bytearray)):
            return
        name = getattr(handler, '__qualname__', repr(handler))
        if response.status not in self.responses:
            raise ResponseMismatch(f'{name} returned {response.status}, which it does not declare')
        adapter = self.responses[response.status]
        if adapter is None:
            return
        try:
            adapter.validate_python(json.loads(body))
        except (ValidationError, JSONDecodeError) as error:
            raise ResponseMismatch(f'{name} returned a {response.status} body it does not declare: {error}') from error


class ResponseMismatch(AssertionError):  # noqa: N818 - not an Error; it is an assertion
    """A handler answered with something other than what it declared."""


def _place(declared: list[Declared], node: str, validated: BaseModel, args: list[Any], kwargs: dict[str, Any]) -> None:
    """Put each validated value where the signature expects it."""
    for item in declared:
        if item.place != node:
            continue
        value = getattr(validated, item.name)
        if item.positional:
            args.append(value)
        else:
            kwargs[item.name] = value


def _collect(node: str, request: web.Request, declared: list[Declared]) -> dict[str, Any]:
    """The raw values for one node, keyed by the handler's parameter names.

    Only declared parameters are looked up, so an undeclared query parameter or
    header is ignored rather than offered to the model.
    """
    fields = [item for item in declared if item.place == node]
    if node == URI:
        return {item.name: request.match_info[item.wire] for item in fields if item.wire in request.match_info}
    if node == HEADER:
        # Case-insensitively: `X-Request-Id` and `x-request-id` are one header.
        available = {key.lower(): value for key, value in request.headers.items()}
        return {item.name: available[item.wire.lower()] for item in fields if item.wire.lower() in available}
    return _query(request, fields)


def _query(request: web.Request, fields: list[Declared]) -> dict[str, Any]:
    """The query string, with repeated keys collected for a sequence parameter.

    `?tags=a&tags=b` is one parameter with two values. A parameter whose
    annotation accepts a sequence gets a list even when one value arrived, so
    `?tags=a` is `['a']` rather than `'a'`.
    """
    out: dict[str, Any] = {}
    for item in fields:
        values = request.query.getall(item.wire, [])
        if not values:
            continue
        out[item.name] = values if len(values) > 1 or _is_sequence(item.annotation) else values[0]
    return out


async def _multipart(request: web.Request, parts: list[Declared], kwargs: dict[str, Any]) -> web.Response | None:
    """Prepare a `multipart/form-data` body without reading the files in it.

    Non-file fields come first and are read here: a value the handler receives
    has to be validated before it runs. Each file becomes an `UploadedFile` that
    has read nothing, and the part is pulled off the wire when the handler asks
    -- which is what keeps a large upload out of memory.

    `read_signature` has already refused a field declared after a file, so the
    eager half is always a prefix of the stream.
    """
    if not (request.content_type or '').startswith('multipart/'):
        return _failures([describe('body', 'a multipart/form-data body is required', kind='multipart_required')])
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError):
        return _failures([describe('body', 'the multipart body could not be read', kind='multipart_invalid')])

    stream = Parts(reader, [item.wire for item in parts])
    for item in parts:
        if item.is_file:
            kwargs[item.name] = UploadedFile(stream, item.wire, item.file or File())
            continue
        try:
            part = await stream.next(item.wire)
        except PartRejected as refused:
            return _failures([refused.detail])
        failure = await _read_field(part, item, kwargs)
        if failure is not None:
            return failure
    return None


async def _read_field(part: Any, item: Declared, kwargs: dict[str, Any]) -> web.Response | None:
    """One non-file part: JSON where it parses as JSON, otherwise its text."""
    raw: Any = await part.text()
    try:
        raw = json.loads(raw)
    except JSONDecodeError:
        if (part.headers.get('Content-Type') or '').startswith('application/json'):
            return _failures([describe('body', 'the part is not valid JSON', kind='json_invalid', loc=[item.wire])])
    try:
        kwargs[item.name] = TypeAdapter(item.annotation).validate_python(raw)
    except ValidationError as error:
        return error_response(error, BODY)
    return None


def _is_sequence(annotation: Any) -> bool:
    """Does this annotation accept many values? `?a=1&a=2` depends on it."""
    origin = get_origin(annotation)
    if origin in (list, set, frozenset, tuple):
        return True
    return any(_is_sequence(arg) for arg in get_args(annotation)) if origin is not None else False


def bind(declared: list[Declared], responses: Sequence[Responds]) -> Bound:
    """Compile a handler's declarations once, at decoration time."""
    models: dict[str, type[BaseModel]] = {}
    for node, label in ((URI, 'Uri'), (QUERY, 'Query'), (HEADER, 'Headers')):
        fields = [item for item in declared if item.place == node]
        if fields:
            models[node] = _model(f'{label}Model', fields)

    bodies = [item for item in declared if item.place == BODY]
    parts = bodies if any(item.is_file for item in bodies) else []
    body = None if parts else next(iter(bodies), None)
    body_adapter = TypeAdapter(body.annotation) if body is not None else None
    described = _with_request_error(declared, responses)

    return Bound(
        declared=declared,
        models=models,
        body=body,
        body_adapter=body_adapter,
        parts=parts,
        declared_responses=described,
        responses={item.code: (TypeAdapter(item.body) if item.body is not None else None) for item in described},
    )


def _with_request_error(declared: list[Declared], responses: Sequence[Responds]) -> list[Responds]:
    """Add the 400 this package answers with, unless the handler declared one.

    A handler taking no parameter cannot fail validation, so it gets none. A
    handler that declared its own 400 keeps it: the document says what the
    handler says, and nothing is added over the top.
    """
    out = list(responses)
    if not declared or any(item.code == STATUS for item in out):
        return out
    out.append(Responds(STATUS, list[RequestError], 'the request did not validate'))
    return out
