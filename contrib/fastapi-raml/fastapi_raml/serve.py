"""Serve a running app's RAML description, and the tree projection of it.

Two routes, neither in the app's own schema, plus an optional viewer:

| URL | Response |
|-----|----------|
| `/raml` | the RAML source, as `application/raml+yaml` |
| `/raml.json` | `fastraml tree` output for it, which is what the viewer reads |
| `/raml-viewer` | the `fastraml-viewer` bundle, when that package is installed |

**This package renders nothing itself.** The viewer reads `api.json` beside
itself, so the mount serves this app's tree at that name and the bundle is
pointed at the right document by where it is mounted.

Behind the first two is `raml_document.serve.build`: the rendered document is
parsed back with `validate=True` before anything is served, and one that does
not parse raises `BuildError` rather than reaching a client.

Nothing runs at import time. The first request builds, in a worker thread so the
event loop keeps serving; the result is held and reused until the app's routes
change, so a route registered after `add_raml_routes` is picked up by the next
request. `build(app)` is the same check, callable from a test or at startup.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from raml_document import serve
from raml_document.serve import RAML_MEDIA_TYPE, BuildError, Served, media_type
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse, Response

from fastapi_raml.render import render, routes_of

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request

__all__ = ['RAML_MEDIA_TYPE', 'BuildError', 'Served', 'add_raml_routes', 'build']

logger = logging.getLogger('fastapi_raml')


def build(app: Any, *, strict: bool = False) -> Served:
    """Render `app` to RAML, parse it back, and project the tree.

    Raises `BuildError` if the rendered document does not parse -- or, with
    `strict=True`, if the renderer had to leave anything out. A test that calls
    `build(app, strict=True)` fails on either, before a client ever asks. What
    was left out is logged as warnings on the `fastapi_raml` logger.
    """
    return serve.build(render(app), strict=strict, log=logger)


@dataclass(slots=True)
class _Cache:
    """One build -- or the failure of one -- and the routes it was made from.

    Keyed on the route objects themselves, held so none can be collected and
    its identity reused. A context FastAPI builds per call answers
    `original_route`, which is the object `add_api_route` made.
    """

    routes: tuple[Any, ...] | None = None
    built: Served | BuildError | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, app: Any) -> Served:
        with self.lock:
            routes = tuple(getattr(route, 'original_route', route) for route in routes_of(app))
            current = self.routes is not None and len(routes) == len(self.routes)
            if not (current and all(a is b for a, b in zip(routes, self.routes or (), strict=True))):
                try:
                    self.built = build(app)
                except BuildError as error:
                    # Held too: a failing document fails the same way until a
                    # route changes, and rendering it on every request is
                    # only slower.
                    logger.exception('RAML for %r does not build', app.title)
                    self.built = error
                self.routes = routes
            if isinstance(self.built, BuildError):
                raise self.built
            assert self.built is not None  # noqa: S101 - set above
            return self.built


def _mount_viewer(app: Any, path: str | None, tree: Callable[[Request], Awaitable[Response]]) -> str | None:
    """Mount the `fastraml-viewer` bundle at `path`, if the package is there.

    Optional on purpose, and silent when absent. The viewer is a convenience
    over the two routes that carry the actual content, so a missing frontend
    package must not stop an app serving its own RAML -- and `fastapi-raml`
    declaring a hard dependency on a pile of JavaScript would be the wrong
    trade for everyone who only wants `/raml`.

    **`api.json` is routed before the mount, and that is the whole trick.** The
    bundle fetches `api.json` beside itself, and it ships one -- the worked
    bookstore, so the package is a standalone demo. Mounted under a real app
    that sample would answer instead of the app's own description, which is a
    convincing wrong answer rather than a visible failure. Starlette matches
    routes in order, so registering this first shadows the shipped file.
    """
    if path is None:
        return None
    try:
        from fastraml_viewer import static_dir  # noqa: PLC0415 - optional extra
        from starlette.staticfiles import StaticFiles  # noqa: PLC0415 - only this path needs it
    except ImportError:
        return None
    app.add_route(f'{path.rstrip("/")}/api.json', tree, include_in_schema=False)
    # `html=True` so `/raml-viewer/` serves index.html; the bundle is built with
    # vite `base: './'`, so its assets resolve under whatever path it lands on.
    app.mount(path, StaticFiles(directory=static_dir(), html=True), name='raml-viewer')
    return path


def _failure(error: BuildError) -> PlainTextResponse:
    """A 500 that says why, rather than one that says nothing.

    The message names the app's own models and routes and quotes the RAML it
    rendered -- nothing `/raml` would not have served had the build succeeded.
    """
    return PlainTextResponse(str(error), status_code=500)


def add_raml_routes(
    app: Any,
    *,
    raml_url: str = '/raml',
    tree_url: str = '/raml.json',
    mount_viewer: str | None = '/raml-viewer',
    include_in_schema: bool = False,
) -> Any:
    """Add the RAML routes to `app`, cached the way `app.openapi()` is cached.

    Call after every route is registered, or at least before the first request:
    the cache is rebuilt when the app's routes change, but the *routes added
    here* have to exist before a request can reach them.

    `mount_viewer` serves the bundle from the **`fastraml-viewer`** package at
    that path, when it is installed -- `pip install fastapi-raml[viewer]`.
    Absent the package nothing is mounted and nothing fails, because the two
    routes above carry the content and a missing frontend must not stop an app
    describing itself. Pass `None` to leave it off.

    A viewer you host yourself needs no argument here: serve this app's
    `{tree_url}` as `api.json` beside your copy of the bundle.

    Raises `ValueError` if `raml_url` or `tree_url` is already routed -- a
    second call would add routes the first ones shadow. Returns the app, so the
    call chains.
    """
    taken = {getattr(route, 'path', None) for route in routes_of(app)}
    for url in (raml_url, tree_url):
        if url in taken:
            raise ValueError(f'{url} is already routed; add_raml_routes adds it once')

    cache = _Cache()

    async def raml_source(request: Request) -> Response:
        try:
            served = await run_in_threadpool(cache.get, app)
        except BuildError as error:
            return _failure(error)
        accept = request.headers.get('accept', '')
        return Response(served.text, media_type=media_type(accept), headers={'Vary': 'Accept'})

    async def raml_tree(request: Request) -> Response:  # noqa: ARG001 - the signature Starlette calls
        try:
            served = await run_in_threadpool(cache.get, app)
        except BuildError as error:
            return _failure(error)
        return Response(served.tree_json, media_type='application/json')

    _mount_viewer(app, mount_viewer, raml_tree)
    app.add_route(raml_url, raml_source, include_in_schema=include_in_schema)
    app.add_route(tree_url, raml_tree, include_in_schema=include_in_schema)

    return app
