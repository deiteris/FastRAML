"""The built `viewer/` SPA, as an installable package.

`viewer/` is a React app over `fastraml tree` output (docs/16 section 11). In a
checkout you build it and serve `viewer/dist` yourself. That advice is useless
to someone who installed from PyPI and has no checkout, which is why the assets
ship here instead.

This package holds **no code that knows about RAML**. It is a directory of
static files, a function that says where they are, and a server that runs them
with a document, so anything that can mount a directory -- FastAPI, aiohttp, a
plain file server -- can serve it:

    from fastraml_viewer import static_dir
    app.mount('/raml-viewer', StaticFiles(directory=static_dir(), html=True))

The bundle is built with vite `base: './'`, so it runs from any sub-path
without being told where it was mounted. Point it at a document with
`api.json` beside `index.html`, so a host serves its own document there -- or
hand the document to `serve`, which does the pointing for you:

    from fastraml_viewer import serve
    serve(tree, host='127.0.0.1', port=8000).serve_forever()

`document` is whatever `json.dumps` accepts. This module never inspects its
shape: the contract is the one the SPA states, which is that `api.json` beside
the entry point holds the output of `fastraml tree`.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from os import PathLike

__all__ = ['__version__', 'index_html', 'serve', 'static_dir']

__version__ = '0.1.0'

#: Populated at build time from `viewer/dist` by `hatch_build.py`. Absent in a
#: checkout until the viewer is built, which is why `static_dir` checks.
_STATIC = Path(__file__).parent / 'static'

#: The one non-static route: the document the SPA reads on load.
_DOCUMENT_ROUTE = '/api.json'


def static_dir() -> Path:
    """The directory holding `index.html` and its assets.

    Raises if the package was built without the bundle. That is a broken
    install rather than a state to handle: the wheel cannot be produced without
    the assets, so reaching this means someone imported from a source tree.
    """
    if not (_STATIC / 'index.html').is_file():
        message = (
            f'the viewer bundle is missing from {_STATIC}. '
            'In a checkout, run `npm ci && npm run build` in viewer/ and reinstall this package.'
        )
        raise RuntimeError(message)
    return _STATIC


def index_html() -> Path:
    """The entry point, for a server that wants to name one file."""
    return static_dir() / 'index.html'


class ViewerServer(ThreadingHTTPServer):
    """The bundle and its document, on one socket.

    `daemon_threads` because the page under view holds keep-alive connections
    open, and Ctrl-C must not wait for the browser to notice.
    """

    daemon_threads = True


def serve(document: object, *, host: str = '127.0.0.1', port: int = 8000) -> ViewerServer:
    """Serve the bundle with `document` as the `api.json` the SPA reads.

    The document route is registered in front of the static files, and that is
    the whole trick: the bundle ships its own `api.json` (the worked sample, so
    the package demos bare), and without the shadow it would answer that
    instead of the document you passed -- a convincing wrong answer rather than
    a visible failure.

    The server is not running when this returns: start it with
    `serve_forever()`. `port=0` binds an ephemeral port, and
    `server.server_address[1]` then names the one it chose.
    """
    payload = (json.dumps(document, indent=2) + '\n').encode('utf-8')
    return ViewerServer((host, port), _handler_for(static_dir(), payload))


def _handler_for(directory: Path, payload: bytes) -> type[BaseHTTPRequestHandler]:
    """A request handler that carries the bundle directory and the document.

    A class per call rather than state on one shared class: two servers in one
    process (the tests run them) must each keep their own document.
    """

    class _Handler(SimpleHTTPRequestHandler):
        # Keep-alive, so the browser's asset requests do not each pay a
        # handshake; every response path sets Content-Length, which is what
        # 1.1 requires. The idle timeout drops a browser tab that is closed
        # mid-page rather than pinning its thread until process exit.
        protocol_version = 'HTTP/1.1'
        timeout = 60

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self) -> None:
            if urlsplit(self.path).path == _DOCUMENT_ROUTE:
                self._send_document()
                return
            super().do_GET()

        def do_HEAD(self) -> None:
            # Without this, HEAD /api.json would report the *shipped* sample's
            # size rather than the document's.
            if urlsplit(self.path).path == _DOCUMENT_ROUTE:
                self._send_document(head_only=True)
                return
            super().do_HEAD()

        def _send_document(self, *, head_only: bool = False) -> None:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def list_directory(self, path: str | PathLike[str]) -> None:  # noqa: ARG002 - an override; keep the base's parameter name
            # A directory without an index is a 404, not a listing of the
            # bundle's internals. The base class opens one; the 404 it returns
            # is what `send_head` receives as "no file".
            self.send_error(404, 'File not found')

    return _Handler
