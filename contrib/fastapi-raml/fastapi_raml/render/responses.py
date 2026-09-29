"""A route's responses: its own, and each one `responses=` adds."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Final

from fastapi.datastructures import DefaultPlaceholder
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from raml_document import Body, Response, TypeDecl
from raml_document.from_pydantic import Shape

if TYPE_CHECKING:
    from raml_document import Method
    from raml_document.from_pydantic import Walk

__all__ = ['responses']

#: What FastAPI's default response class says when a route says nothing.
DEFAULT_RESPONSE_DESCRIPTION: Final = 'Successful Response'


def _response_class(route: Any) -> type:
    """The route's response class, unwrapped from FastAPI's `Default(...)` marker."""
    response_class = route.response_class
    if isinstance(response_class, DefaultPlaceholder):
        response_class = response_class.value
    return response_class  # type: ignore[no-any-return]


def _payload(route: Any, info: Any, at: str, walk: Walk) -> TypeDecl:
    """A response body's type: what the model *writes*, as the route's settings have it write.

    `response_model_by_alias` and `response_model_exclude_none` are a `Shape`;
    `response_model_include` and `response_model_exclude` leave fields out,
    which is a type of its own, declared for this route.
    """
    shape = Shape(
        by_alias=getattr(route, 'response_model_by_alias', True),
        exclude_none=getattr(route, 'response_model_exclude_none', False),
    )
    include = getattr(route, 'response_model_include', None)
    exclude = getattr(route, 'response_model_exclude', None)
    with walk.output(shape):
        if include is None and exclude is None:
            return walk.field(info, at)
        model = info.annotation
        name = f'{getattr(model, "__name__", "Response")}{shape.suffix}_{route.name}'
        subset = walk.subset(model, name, at, include=include, exclude=exclude)
    if subset is None:
        walk.drop(at, f'response_model_include or _exclude on {model!r}, which is not a model; written as any')
        return TypeDecl(type='any')
    return subset


def _status_code(route: Any, response_class: type) -> int:
    """The status the route answers with: its own, else its response class's default.

    `RedirectResponse` answers 307 unless told otherwise, and FastAPI's schema
    reads the default off the class's `__init__` in just this way.
    """
    if route.status_code is not None:
        return int(route.status_code)
    parameter = inspect.signature(response_class).parameters.get('status_code')
    if parameter is not None and isinstance(parameter.default, int):
        return parameter.default
    return 200


def responses(route: Any, method: Method, at: str, walk: Walk) -> None:
    """The route's own response, then each one `responses=` adds."""
    response_class = _response_class(route)
    media = getattr(response_class, 'media_type', None)
    code = _status_code(route, response_class)
    response = method.responses.setdefault(code, Response())
    if route.response_description != DEFAULT_RESPONSE_DESCRIPTION:
        response.description = route.response_description
    if is_body_allowed_for_status_code(code):
        response.body = _route_body(route, response_class, media, f'{at}.{code}', walk)

    for key, spec in (route.responses or {}).items():
        # FastAPI accepts `"default"` and a range such as `"4XX"`. RAML keys a
        # response by its status code and has neither.
        if not (isinstance(key, int) or (isinstance(key, str) and key.isdigit())):
            what = 'a default response' if key == 'default' else f'the status range {key!r}'
            walk.drop(at, f'{what} has no RAML form; a response is keyed by its status code')
            continue
        extra = method.responses.setdefault(int(key), Response())
        if spec.get('description'):
            extra.description = spec['description']
        # Joined to what the route's own response already says, not over it:
        # `responses={200: {'content': {'image/png': {}}}}` adds a media type.
        body = extra.body or Body()
        for extra_media, entry in (spec.get('content') or {}).items():
            if (entry or {}).get('schema'):
                walk.drop(f'{at}.{key}', f'a JSON Schema for {extra_media} is not translated; written as any')
            body.content.setdefault(extra_media, TypeDecl(type='any'))
        if spec.get('model') is not None:
            # FastAPI puts a `model` under the route's own media type.
            with walk.output():
                body.content[media or 'application/json'] = walk.annotation(spec['model'], f'{at}.{key}')
        if spec.get('headers'):
            walk.drop(f'{at}.{key}', 'response headers are not described')
        if body.content:
            extra.body = body


def _route_body(route: Any, response_class: type, media: str | None, at: str, walk: Walk) -> Body | None:
    """The body of the route's own response, as FastAPI's schema would state it."""
    if getattr(route, 'is_json_stream', False) or getattr(route, 'is_sse_stream', False):
        stream = 'application/jsonl' if route.is_json_stream else 'text/event-stream'
        walk.drop(at, f'a {stream} stream of items has no RAML form; written as any')
        return Body({stream: TypeDecl(type='any')})
    if media is None:
        return None
    if not issubclass(response_class, JSONResponse):
        # `HTMLResponse`, `PlainTextResponse`: text, whatever the handler's
        # annotation says it builds the text from.
        return Body({media: TypeDecl(type='string')})
    if route.response_field is None:
        return Body({media: TypeDecl(type='any')})
    return Body({media: _payload(route, route.response_field.field_info, at, walk)})
