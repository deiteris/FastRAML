from __future__ import annotations

import dataclasses
import inspect
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from aiohttp import web
from pyraml import ParseOptions, Raml, parse_from_path

from raml_mock.errors import MockGenerationError, RequestValidationError
from raml_mock.media import base_media_type
from raml_mock.request import DecodedRequest, decode_request
from raml_mock.responses import build_response
from raml_mock.routes import MockRoute, RouteTable

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterator, Awaitable, Mapping

    from raml_mock.codecs import BodyCodec

__all__ = ['MockRequest', 'MockServer', 'ResponseHandler', 'create_app', 'create_app_from_raml', 'mock_server']

_SERVER_ERROR = 500
_RAML_KEY = web.AppKey('raml', Raml)


@dataclass(slots=True, frozen=True)
class MockRequest:
    """A matched aiohttp request and its RAML-validated values."""

    request: web.Request
    route: MockRoute
    values: DecodedRequest


class ResponseHandler(Protocol):
    """An optional response override for one method and RAML path."""

    def __call__(self, request: MockRequest) -> web.StreamResponse | Awaitable[web.StreamResponse]: ...


@dataclass(slots=True, frozen=True)
class MockServer:
    """A running loopback mock and the parsed model behind it."""

    app: web.Application
    raml: Raml
    url: str


def create_app(
    source: str | os.PathLike[str],
    *,
    options: ParseOptions | None = None,
    overrides: Mapping[tuple[str, str], ResponseHandler] | None = None,
    codecs: Mapping[str, BodyCodec] | None = None,
) -> web.Application:
    """Parse *source* and return an aiohttp application for its operations."""
    effective = dataclasses.replace(options or ParseOptions(), unwrap=True, validate=True)
    return create_app_from_raml(parse_from_path(source, effective), overrides=overrides, codecs=codecs)


def create_app_from_raml(
    raml: Raml,
    *,
    overrides: Mapping[tuple[str, str], ResponseHandler] | None = None,
    codecs: Mapping[str, BodyCodec] | None = None,
) -> web.Application:
    """Return an aiohttp application backed by an already parsed RAML model."""
    table = RouteTable(raml)
    configured = {(method.upper(), path): handler for (method, path), handler in (overrides or {}).items()}
    configured_codecs = {base_media_type(media): codec for media, codec in (codecs or {}).items()}
    known = {(route.method, route.path) for route in table.routes}
    unknown = set(configured) - known
    if unknown:
        method, path = min(unknown)
        raise ValueError(f'override does not name a RAML operation: {method} {path}')

    async def dispatch(request: web.Request) -> web.StreamResponse:
        matches = table.paths(request.path)
        if not matches:
            return _problem(404, 'not found')
        method = 'GET' if request.method == 'HEAD' else request.method
        selected = next(((route, values) for route, values in matches if route.method == method), None)
        if selected is None:
            allowed_set = {route.method for route, _values in matches}
            if 'GET' in allowed_set:
                allowed_set.add('HEAD')
            allowed = sorted(allowed_set)
            return _problem(405, 'method not allowed', headers={'Allow': ', '.join(allowed)})
        route, path_values = selected
        try:
            values = await decode_request(
                request,
                route.operation.request,
                route.endpoint.uri_parameters,
                path_values,
                configured_codecs,
            )
            override = configured.get((route.method, route.path))
            if override is not None:
                response = override(MockRequest(request, route, values))
                if inspect.isawaitable(response):
                    response = await response
                return response
            return build_response(request, route.operation, configured_codecs)
        except RequestValidationError as error:
            return _problem(
                error.status,
                'invalid request' if error.status < _SERVER_ERROR else 'mock response failed',
                issues=[issue.as_dict() for issue in error.issues],
            )
        except MockGenerationError as error:
            return _problem(500, 'mock generation failed', issues=[{'location': 'response', 'message': str(error)}])

    app = web.Application()
    app[_RAML_KEY] = raml
    app.router.add_route('*', '/{path:.*}', dispatch)
    return app


@asynccontextmanager
async def mock_server(
    source: str | os.PathLike[str],
    *,
    options: ParseOptions | None = None,
    overrides: Mapping[tuple[str, str], ResponseHandler] | None = None,
    codecs: Mapping[str, BodyCodec] | None = None,
    host: str = '127.0.0.1',
) -> AsyncIterator[MockServer]:
    """Run a mock on an ephemeral loopback port for the context's lifetime."""
    app = create_app(source, options=options, overrides=overrides, codecs=codecs)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        sock.setblocking(False)  # noqa: FBT003 - socket API requires the positional flag
        port = int(sock.getsockname()[1])
        site = web.SockSite(runner, sock)
        await site.start()
        yield MockServer(app, app[_RAML_KEY], f'http://{host}:{port}')
    finally:
        await runner.cleanup()
        sock.close()


def _problem(
    status: int,
    message: str,
    *,
    issues: list[dict[str, object]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> web.Response:
    content: dict[str, object] = {'error': message}
    if issues:
        content['issues'] = issues
    return web.json_response(content, status=status, headers=headers)
