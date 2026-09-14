from __future__ import annotations

import asyncio
import dataclasses
import inspect
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from aiohttp import web
from fastraml import ParseOptions, Raml, parse_from_path

from raml_mock.auth import authenticate
from raml_mock.config import MockOptions, RouteBehavior
from raml_mock.errors import MockGenerationError, RequestValidationError
from raml_mock.media import base_media_type
from raml_mock.request import DecodedRequest, decode_request
from raml_mock.responses import ResponseContext, build_response
from raml_mock.routes import MockRoute, RouteTable
from raml_mock.state import MockState, StateResult
from raml_mock.status import FORBIDDEN, is_success

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterator, Awaitable, Mapping

    from raml_mock.codecs import BodyCodec

__all__ = [
    'MockRequest',
    'MockServer',
    'ResponseHandler',
    'ValidationStatusMapper',
    'create_app',
    'create_app_from_raml',
    'mock_server',
    'state_of',
]

_SERVER_ERROR = 500
_BAD_REQUEST = 400
_RAML_KEY = web.AppKey('raml', Raml)
_STATE_KEY = web.AppKey('state', MockState)


@dataclass(slots=True, frozen=True)
class MockRequest:
    """A matched aiohttp request and its RAML-validated values."""

    request: web.Request
    route: MockRoute
    values: DecodedRequest


class ResponseHandler(Protocol):
    """An optional response override for one method and RAML path."""

    def __call__(self, request: MockRequest) -> web.StreamResponse | Awaitable[web.StreamResponse]: ...


class ValidationStatusMapper(Protocol):
    """Choose the status for a RAML request-validation failure."""

    def __call__(self, route: MockRoute, error: RequestValidationError, /) -> int: ...


@dataclass(slots=True, frozen=True)
class MockServer:
    """A running loopback mock and the parsed model behind it."""

    app: web.Application
    raml: Raml
    url: str
    state: MockState


def create_app(  # noqa: PLR0913 - parse, transport, and mock policies are independent
    source: str | os.PathLike[str],
    *,
    options: ParseOptions | None = None,
    overrides: Mapping[tuple[str, str], ResponseHandler] | None = None,
    codecs: Mapping[str, BodyCodec] | None = None,
    validation_status: ValidationStatusMapper | None = None,
    mock_options: MockOptions | None = None,
) -> web.Application:
    """Parse *source* and return an aiohttp application for its operations."""
    effective = dataclasses.replace(options or ParseOptions(), unwrap=True, validate=True)
    return create_app_from_raml(
        parse_from_path(source, effective),
        overrides=overrides,
        codecs=codecs,
        validation_status=validation_status,
        mock_options=mock_options,
    )


def create_app_from_raml(
    raml: Raml,
    *,
    overrides: Mapping[tuple[str, str], ResponseHandler] | None = None,
    codecs: Mapping[str, BodyCodec] | None = None,
    validation_status: ValidationStatusMapper | None = None,
    mock_options: MockOptions | None = None,
) -> web.Application:
    """Return an aiohttp application backed by an already parsed RAML model."""
    table = RouteTable(raml)
    options = mock_options or MockOptions()
    dispatcher = _Dispatcher(
        table=table,
        overrides={(method.upper(), path): handler for (method, path), handler in (overrides or {}).items()},
        codecs={base_media_type(media): codec for media, codec in (codecs or {}).items()},
        behaviors={(method.upper(), path): value for (method, path), value in options.routes.items()},
        options=options,
        state=MockState(options.resources, {(route.method, route.path): route for route in table.routes}),
        validation_status=validation_status,
    )
    dispatcher.check()

    app = web.Application()
    app[_RAML_KEY] = raml
    app[_STATE_KEY] = dispatcher.state
    # The bound method, not the instance: aiohttp tests the handler with
    # `iscoroutinefunction`, which is False for a callable object however its
    # `__call__` is declared, and quietly routes it through the bare-function
    # compatibility path.
    app.router.add_route('*', '/{path:.*}', dispatcher.handle)
    return app


@dataclass(slots=True, frozen=True)
class _Dispatcher:
    """One request, resolved through the model and the configured behaviour."""

    table: RouteTable
    overrides: Mapping[tuple[str, str], ResponseHandler]
    codecs: Mapping[str, BodyCodec]
    behaviors: Mapping[tuple[str, str], RouteBehavior]
    options: MockOptions
    state: MockState
    validation_status: ValidationStatusMapper | None

    def check(self) -> None:
        """Reject configuration that names something the RAML does not."""
        routes = {(route.method, route.path): route for route in self.table.routes}
        unknown = (set(self.overrides) | set(self.behaviors) | self.state.routes) - set(routes)
        if unknown:
            method, path = min(unknown)
            raise ValueError(f'override does not name a RAML operation: {method} {path}')
        for key, behavior in self.behaviors.items():
            if behavior.status is not None and not _declares_status(routes[key].operation.responses, behavior.status):
                raise ValueError(f'configured status is not declared for {key[0]} {key[1]}: {behavior.status}')

    async def handle(self, request: web.Request) -> web.StreamResponse:
        matched = self._match(request)
        if isinstance(matched, web.Response):
            return matched
        route, path_values = matched
        decision = await authenticate(request, route, self.options.authentication)
        if not decision.allowed:
            return _problem(
                decision.status,
                'forbidden' if decision.status == FORBIDDEN else 'unauthorized',
                headers=decision.headers,
            )
        try:
            return await self._answer(request, route, path_values)
        except RequestValidationError as error:
            return _validation_problem(route, error, self.validation_status)
        except MockGenerationError as error:
            return _problem(500, 'mock generation failed', issues=[{'location': 'response', 'message': str(error)}])
        except (TypeError, ValueError) as error:
            return _problem(500, 'stateful resource failed', issues=[{'location': 'state', 'message': str(error)}])

    def _match(self, request: web.Request) -> tuple[MockRoute, Mapping[str, str]] | web.Response:
        matches = self.table.paths(request.path)
        if not matches:
            return _problem(404, 'not found')
        method = 'GET' if request.method == 'HEAD' else request.method
        selected = next(((route, values) for route, values in matches if route.method == method), None)
        if selected is not None:
            return selected
        allowed = {route.method for route, _values in matches}
        if 'GET' in allowed:
            allowed.add('HEAD')
        return _problem(405, 'method not allowed', headers={'Allow': ', '.join(sorted(allowed))})

    async def _answer(self, request: web.Request, route: MockRoute, path: Mapping[str, str]) -> web.StreamResponse:
        key = (route.method, route.path)
        values = await decode_request(
            request, route.operation.request, route.endpoint.uri_parameters, path, self.codecs
        )
        behavior = self.behaviors.get(key, RouteBehavior())
        if behavior.delay:
            await asyncio.sleep(behavior.delay)
        override = self.overrides.get(key)
        if override is not None:
            response = override(MockRequest(request, route, values))
            return await response if inspect.isawaitable(response) else response
        context = ResponseContext(behavior, self.options.generation, f'{route.method} {route.path}')
        return self._from_state(request, route, self.state.handle(key, values), context)

    def _from_state(
        self, request: web.Request, route: MockRoute, result: StateResult | None, context: ResponseContext
    ) -> web.Response:
        if result is None:
            return build_response(request, route.operation, self.codecs, context)
        if result.status is not None:
            if not _declares_status(route.operation.responses, result.status):
                return _problem(result.status, 'stateful resource operation failed')
            bare = dataclasses.replace(context.behavior, status=str(result.status), example=None)
            return build_response(request, route.operation, self.codecs, dataclasses.replace(context, behavior=bare))
        response = build_response(
            request, route.operation, self.codecs, dataclasses.replace(context, value=result.value)
        )
        # `build_response` does not suspend, so nothing interleaves between the
        # check the store made and this commit.
        if result.commit is not None and is_success(response.status):
            result.commit()
        return response


@asynccontextmanager
async def mock_server(  # noqa: PLR0913 - mirrors `create_app` plus the listener host
    source: str | os.PathLike[str],
    *,
    options: ParseOptions | None = None,
    overrides: Mapping[tuple[str, str], ResponseHandler] | None = None,
    codecs: Mapping[str, BodyCodec] | None = None,
    validation_status: ValidationStatusMapper | None = None,
    mock_options: MockOptions | None = None,
    host: str = '127.0.0.1',
) -> AsyncIterator[MockServer]:
    """Run a mock on an ephemeral loopback port for the context's lifetime."""
    app = create_app(
        source,
        options=options,
        overrides=overrides,
        codecs=codecs,
        validation_status=validation_status,
        mock_options=mock_options,
    )
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        sock.setblocking(False)  # noqa: FBT003 - socket API requires the positional flag
        port = int(sock.getsockname()[1])
        site = web.SockSite(runner, sock)
        await site.start()
        yield MockServer(app, app[_RAML_KEY], f'http://{host}:{port}', app[_STATE_KEY])
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


def _declares_status(responses: Mapping[str, object], status: int | str) -> bool:
    # An exact key and nothing else. RAML has no `4xx` response class, so the
    # parser never produces one to match against (docs/08 section 3).
    return str(status) in responses


def _validation_problem(
    route: MockRoute,
    error: RequestValidationError,
    mapper: ValidationStatusMapper | None,
) -> web.Response:
    status = mapper(route, error) if mapper is not None and error.status == _BAD_REQUEST else error.status
    if status != error.status and not _declares_status(route.operation.responses, status):
        return _problem(
            500,
            'mock response failed',
            issues=[
                {
                    'location': 'response',
                    'message': 'validation status mapper selected an undeclared response',
                    'detail': {'status': status},
                }
            ],
        )
    return _problem(
        status,
        'invalid request' if status < _SERVER_ERROR else 'mock response failed',
        issues=[issue.as_dict() for issue in error.issues],
    )


def state_of(app: web.Application) -> MockState:
    """Return the isolated state controller owned by *app*."""
    return app[_STATE_KEY]
