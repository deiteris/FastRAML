"""Serve a running app's RAML description, and the tree projection of it.

Three routes, none of them in the app's own schema:

| URL | Response |
|-----|----------|
| `/raml` | the RAML source, as `application/raml+yaml` |
| `/raml.json` | `fastraml tree` output for it, which is what `viewer/` reads |
| `/raml-docs` | an HTML stub naming both |

The pipeline behind the first two:

    app --render()--> RAML text --parse_from_string()--> Raml --build_tree()--> tree

**The parse is not a formality.** It runs with `validate=True`, so a document
that will not parse, or whose examples do not validate, raises here rather than
reaching a client. It is also the only route to the third stage: `build_tree`
projects a parsed `Raml`, so nothing reaches the viewer without going through
RAML text first.

Nothing runs at import time. The first request builds; the result is held and
reused, keyed on the router's `_get_routes_version()`, so a route registered
after `add_raml_routes` is picked up by the next request.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastraml import ParseOptions, parse_from_string
from fastraml.views.tree import build_tree
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse

from fastapi_raml.render import render

if TYPE_CHECKING:
    from starlette.requests import Request

__all__ = ['RAML_MEDIA_TYPE', 'Served', 'add_raml_routes', 'build']

#: RAML's registered media type (spec § Introduction).
RAML_MEDIA_TYPE = 'application/raml+yaml'

_STUB = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body>
<h1>{title}</h1>
<p>This API is described in RAML 1.0.</p>
<ul>
  <li><a href="{raml_url}">{raml_url}</a> &mdash; the RAML source</li>
  <li><a href="{tree_url}">{tree_url}</a> &mdash; the same document as <code>fastraml tree</code> output</li>
</ul>
{viewer}
</body>
</html>
"""


def _mount_viewer(app: Any, path: str | None) -> str | None:
    """Mount the `fastraml-viewer` bundle at `path`, if the package is there.

    Optional on purpose, and silent when absent. The viewer is a convenience
    over the two routes that carry the actual content, so a missing frontend
    package must not stop an app serving its own RAML -- and `fastapi-raml`
    declaring a hard dependency on a pile of JavaScript would be the wrong
    trade for everyone who only wants `/raml`.
    """
    if path is None:
        return None
    try:
        from fastraml_viewer import static_dir  # noqa: PLC0415 - optional extra
        from starlette.staticfiles import StaticFiles  # noqa: PLC0415 - only this path needs it
    except ImportError:
        return None
    # `html=True` so `/raml-viewer/` serves index.html; the bundle is built with
    # vite `base: './'`, so its assets resolve under whatever path it lands on.
    app.mount(path, StaticFiles(directory=static_dir(), html=True), name='raml-viewer')
    return path


@dataclass(slots=True)
class _Cache:
    """One render, and the router version it was built from."""

    version: int | None = None
    built: Served | None = None


@dataclass(slots=True)
class Served:
    """One app, rendered and parsed back."""

    #: The RAML source.
    text: str
    #: `fastraml tree` output for it, ready for `json.dumps`.
    tree: Any
    #: Everything the renderer could not express (`Report.dropped`).
    dropped: list[str]


def build(app: Any) -> Served:
    """Render `app` to RAML, parse it back, and project the tree.

    The parse is what makes this more than string formatting: `validate=True`
    would also check every example, and `unwrap=True` is required by the tree
    view, which is the effective document rather than the declared one.

    `parse_from_string` still needs a `base_dir` for a relative `!include` to
    resolve against. Nothing here writes an include, so a throwaway directory is
    the honest answer -- it names a real place without leaving anything in it.
    """
    report = render(app)
    with tempfile.TemporaryDirectory() as directory:
        raml = parse_from_string(
            report.to_raml(),
            file_name='api.raml',
            base_dir=directory,
            options=ParseOptions(unwrap=True, validate=True),
        )
        return Served(text=report.to_raml(), tree=build_tree(raml), dropped=report.dropped)


def add_raml_routes(  # noqa: PLR0913 - one parameter per route it adds, plus two switches
    app: Any,
    *,
    raml_url: str = '/raml',
    tree_url: str = '/raml.json',
    docs_url: str | None = '/raml-docs',
    viewer_url: str | None = None,
    mount_viewer: str | None = '/raml-viewer',
    include_in_schema: bool = False,
) -> Any:
    """Add the RAML routes to `app`, cached the way `app.openapi()` is cached.

    Call after every route is registered, or at least before the first request:
    the cache keys on `_get_routes_version()`, so a later route invalidates it,
    but the *routes added here* have to exist before a request can reach them.

    There are two ways to get a viewer, and they compose:

    * `mount_viewer` serves the bundle from the **`fastraml-viewer`** package at
      that path, when it is installed -- `pip install fastapi-raml[viewer]`.
      Absent the package, nothing is mounted and nothing fails; this is the
      default because the alternative used to be telling a pip user to "build
      `viewer/`", which is a directory they do not have.
    * `viewer_url` names a viewer you host yourself, and wins over the mount.

    Either way the stub links to `{viewer}?src={tree_url}`, because the bundle
    reads the tree projection over HTTP rather than being built around it.

    Returns the app, so the call chains.
    """
    cache: _Cache = _Cache()
    viewer_url = viewer_url or _mount_viewer(app, mount_viewer)

    def served() -> Served:
        version = app.router._get_routes_version()  # noqa: SLF001 - the app's own cache key
        if cache.built is None or cache.version != version:
            cache.built = build(app)
            cache.version = version
        return cache.built

    async def raml_source(request: Request) -> PlainTextResponse:  # noqa: ARG001 - the signature Starlette calls
        return PlainTextResponse(served().text, media_type=RAML_MEDIA_TYPE)

    async def raml_tree(request: Request) -> JSONResponse:  # noqa: ARG001 - as above
        # Through `json.dumps` rather than `JSONResponse(content=...)` so the
        # tree's own encoder-free contract is what reaches the wire.
        return JSONResponse(json.loads(json.dumps(served().tree)))

    app.add_route(raml_url, raml_source, include_in_schema=include_in_schema)
    app.add_route(tree_url, raml_tree, include_in_schema=include_in_schema)

    if docs_url is not None:

        async def raml_docs(request: Request) -> HTMLResponse:
            # `root_path` is read per request, exactly as FastAPI's own docs
            # routes read it, so the links survive being mounted under a prefix.
            root = request.scope.get('root_path', '').rstrip('/')
            tree = root + tree_url
            viewer = (
                f'<p><a href="{root + viewer_url if viewer_url.startswith("/") else viewer_url}?src={tree}">'
                'Open in the viewer</a></p>'
                if viewer_url
                else '<p>No viewer is configured. Install <code>fastapi-raml[viewer]</code>, or serve one '
                'yourself and pass <code>viewer_url=</code> to <code>add_raml_routes</code>.</p>'
            )
            return HTMLResponse(
                _STUB.format(title=f'{app.title} - RAML', raml_url=root + raml_url, tree_url=tree, viewer=viewer)
            )

        app.add_route(docs_url, raml_docs, include_in_schema=include_in_schema)
    return app
