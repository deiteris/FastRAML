"""Serve a running app's RAML description, and the tree projection of it.

Two routes, plus an optional viewer:

| URL | Response |
|-----|----------|
| `/raml` | the RAML source, as `application/raml+yaml` |
| `/raml.json` | `fastraml tree` output for it, which is what the viewer reads |
| `/raml-viewer` | the `fastraml-viewer` bundle, when that package is installed |

**This package renders nothing itself.** The viewer reads `api.json` beside
itself, so the mount serves this app's tree at that name and the bundle is
pointed at the right document by where it is mounted.

The pipeline behind the first two:

    app --render()--> RAML text --parse_from_string()--> Raml --build_tree()--> tree

**The parse is not a formality.** It runs with `validate=True`, so a document
that will not parse, or whose examples do not validate, raises here rather than
reaching a client. It is also the only route to the third stage: `build_tree`
projects a parsed `Raml`, so nothing reaches the viewer without going through
RAML text first.

Nothing runs at import time. The first request builds, and the result is held.
There is no cache key, and there does not need to be one: aiohttp freezes its
router when the application starts, so no route can appear after a request has
been answered. FastAPI's integration keys its cache on a router version for
exactly the reason this one does not.

Everything registered here is marked with `exclude`, so the routes that describe
the app do not describe themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from aiohttp import web
from fastraml import ParseOptions, build_tree, parse_from_string

from aiohttp_raml.decorator import exclude
from aiohttp_raml.render import render

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ['RAML_MEDIA_TYPE', 'Served', 'add_raml_routes', 'build']

#: RAML's registered media type (spec § Introduction).
RAML_MEDIA_TYPE = 'application/raml+yaml'


@dataclass(slots=True)
class Served:
    """One app, rendered and parsed back."""

    #: The RAML source.
    text: str
    #: `fastraml tree` output for it, ready for `json.dumps`.
    tree: Any
    #: Everything the renderer could not express (`Report.dropped`).
    dropped: list[str]


def build(app: web.Application, **metadata: Any) -> Served:
    """Render `app` to RAML, parse it back, and project the tree.

    The parse is what makes this more than string formatting: `validate=True`
    also checks every example, and `unwrap=True` is required by the tree view,
    which is the effective document rather than the declared one.

    `parse_from_string` still needs a `base_dir` for a relative `!include` to
    resolve against. Nothing here writes an include, so a throwaway directory is
    the honest answer -- it names a real place without leaving anything in it.
    """
    report = render(app, **metadata)
    text = report.to_raml()
    with TemporaryDirectory() as directory:
        raml = parse_from_string(
            text,
            file_name='api.raml',
            base_dir=directory,
            options=ParseOptions(unwrap=True, validate=True),
        )
        return Served(text=text, tree=build_tree(raml), dropped=report.dropped)


def _mount_viewer(app: web.Application, path: str, tree: Callable[[web.Request], Awaitable[web.Response]]) -> bool:
    """Mount the `fastraml-viewer` bundle at `path`, if the package is there.

    Optional on purpose, and silent when absent. The viewer is a convenience
    over the two routes that carry the actual content, so a missing frontend
    package must not stop an app serving its own RAML -- and `aiohttp-raml`
    declaring a hard dependency on a pile of JavaScript would be the wrong
    trade for everyone who only wants `/raml`.

    **`api.json` is routed before the static files, and that is the whole
    trick.** The bundle fetches `api.json` beside itself, and it ships one --
    the worked bookstore, so the package is a standalone demo. Mounted under a
    real app that sample would answer instead of the app's own description,
    which is a convincing wrong answer rather than a visible failure. aiohttp
    resolves resources in registration order, so registering this first shadows
    the shipped file.

    aiohttp serves no index for a static route, so `/` under the mount is its
    own handler. The bundle is built with vite `base: './'`, so its assets
    resolve under whatever path it lands on -- which is why the bare mount path
    redirects rather than answering: served at `/raml-viewer`, `./assets/x.js`
    resolves to `/assets/x.js`, and the page loads blank. Starlette's
    `redirect_slashes` does the same for FastAPI.
    """
    try:
        from fastraml_viewer import static_dir  # noqa: PLC0415 - optional extra
    except ImportError:
        return False
    directory = Path(static_dir())
    index = directory / 'index.html'

    @exclude
    async def viewer_index(request: web.Request) -> web.Response:  # noqa: ARG001 - the signature aiohttp calls
        return web.Response(text=index.read_text(encoding='utf-8'), content_type='text/html')

    @exclude
    async def viewer_slash(request: web.Request) -> web.Response:
        query = f'?{request.query_string}' if request.query_string else ''
        raise web.HTTPTemporaryRedirect(f'{path}/{query}')

    app.router.add_get(f'{path}/api.json', tree)
    app.router.add_get(path, viewer_slash)
    app.router.add_get(f'{path}/', viewer_index)
    exclude(app.router.add_static(f'{path}/', directory))
    return True


def add_raml_routes(
    app: web.Application,
    *,
    raml_url: str = '/raml',
    tree_url: str = '/raml.json',
    mount_viewer: str | None = '/raml-viewer',
    **metadata: Any,
) -> web.Application:
    """Add the RAML routes to `app`, and return it so the call chains.

    Call before the application starts; aiohttp freezes the router at startup
    and refuses a route after that. Call it *last*, too -- the description is
    built from the routes registered by the time the first request arrives, and
    aiohttp cannot add one later either way.

    `**metadata` is passed to `render`: `title`, `version`, `description` and
    `base_uri`, none of which an aiohttp application carries.

    `mount_viewer` serves the bundle from the **`fastraml-viewer`** package at
    that path, when it is installed -- `pip install aiohttp-raml[viewer]`.
    Absent the package nothing is mounted and nothing fails, because the two
    routes above carry the content and a missing frontend must not stop an app
    describing itself. Pass `None` to leave it off.

    A viewer you host yourself needs no argument here: serve this app's
    `{tree_url}` as `api.json` beside your copy of the bundle.
    """
    held: list[Served] = []

    def served() -> Served:
        if not held:
            held.append(build(app, **metadata))
        return held[0]

    @exclude
    async def raml_source(request: web.Request) -> web.Response:  # noqa: ARG001 - the signature aiohttp calls
        return web.Response(text=served().text, content_type=RAML_MEDIA_TYPE)

    @exclude
    async def raml_tree(request: web.Request) -> web.Response:  # noqa: ARG001 - as above
        return web.json_response(served().tree)

    if mount_viewer is not None:
        _mount_viewer(app, mount_viewer.rstrip('/'), raml_tree)
    app.router.add_get(raml_url, raml_source)
    app.router.add_get(tree_url, raml_tree)
    return app
