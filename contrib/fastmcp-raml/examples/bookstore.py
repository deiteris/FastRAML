"""Serve the Bookstore API as an MCP server, end to end.

The document is `fixtures/sample`, the repo's worked example: a templated
`baseUri`, a `queryString` in place of `queryParameters`, two media types under
one body, a recursive type, four `documentation:` entries, and security on every
method.

The API it describes does not exist, so this starts one. A stand-in bookstore
runs on a local port, and the MCP server is pointed at it -- so calling a tool
builds a real HTTP request from the RAML, sends it over a real socket, and
validates the reply against the schema the RAML declared. Every request is
logged, which is the part worth watching.

    uv run python examples/bookstore.py              # HTTP, prints a URL
    uv run python examples/bookstore.py --stdio      # for an MCP client config
    uv run python examples/bookstore.py --describe    # what the document became

`fastmcp run examples/bookstore.py` works too: `mcp` is built at import.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import httpx2
from pyraml import ParseOptions

from fastmcp_raml import raml_mcp

if TYPE_CHECKING:
    from fastmcp import FastMCP

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / 'fixtures'
SAMPLE = FIXTURES / 'sample' / 'api.raml'

# `workspace_root` because the document includes from a sibling directory, and
# the loader's sandbox is the entry file's own directory by default.
OPTIONS = ParseOptions(workspace_root=FIXTURES)

# `baseUri` is `https://{tenant}.books.example.com/{version}`, which is not a
# host that answers. Supplying a client overrides it, which is the same thing a
# caller does when the real host is per-deployment.
TENANT = {'tenant': 'acme'}

# ---------------------------------------------------------------------------
# The API the document describes.
#
# Every payload below satisfies the RAML, because the tools validate what comes
# back against the schema `views/jsonschema.py` derived from it. A `title` of ''
# or a `currency` of 'CHF' fails the call rather than passing quietly, which is
# the property worth demonstrating.
# ---------------------------------------------------------------------------

DUNE: dict[str, Any] = {
    'id': 'b-1',
    'createdAt': '2024-01-01T00:00:00Z',
    'title': 'Dune',
    'isbn': '9780441013593',
    'price': {'amount': 9.99, 'currency': 'USD'},
    'tags': ['science-fiction'],
}

LEFT_HAND: dict[str, Any] = {
    'id': 'b-2',
    'createdAt': '2024-02-01T00:00:00Z',
    'title': 'The Left Hand of Darkness',
    'isbn': '9780441478125',
    'price': {'amount': 8.5, 'currency': 'GBP'},
}

CATALOGUE = [DUNE, LEFT_HAND]

DELIVERIES = [
    {
        'address': {'line1': '1 Bridge Street', 'city': 'Bristol', 'postcode': 'BS1 1AA', 'country': 'GB'},
        'promisedFor': '2024-06-02T09:00:00Z',
    }
]

PUBLICATIONS = [
    {'kind': 'Publication', 'title': 'Dune'},
    # `monthly` is `Magazine`'s `discriminatorValue`, so this is the subtype.
    {'kind': 'monthly', 'title': 'Locus', 'issue': 7},
]

SHELF = [{'position': 1, 'book': DUNE, 'note': 'front of house'}]


def _book_for(isbn: str) -> tuple[int, Any]:
    for book in CATALOGUE:
        if book['isbn'] == isbn:
            return 200, book
    return 404, None


#: `(method, path pattern) -> (status, body)`. A body of `None` sends no content,
#: which is what the document's `204` declares.
ROUTES: list[tuple[str, re.Pattern[str], Any]] = [
    ('GET', re.compile(r'^/books$'), lambda _m, _b: (200, CATALOGUE)),
    ('POST', re.compile(r'^/books$'), lambda _m, body: (201, {**DUNE, **(body or {})})),
    ('GET', re.compile(r'^/books/(?P<isbn>[^/]+)$'), lambda m, _b: _book_for(m['isbn'])),
    ('DELETE', re.compile(r'^/books/(?P<isbn>[^/]+)$'), lambda _m, _b: (204, None)),
    ('GET', re.compile(r'^/deliveries$'), lambda _m, _b: (200, DELIVERIES)),
    ('GET', re.compile(r'^/publications$'), lambda _m, _b: (200, PUBLICATIONS)),
    ('GET', re.compile(r'^/search$'), lambda _m, _b: (200, CATALOGUE)),
    ('POST', re.compile(r'^/shelves$'), lambda _m, _b: (201, SHELF)),
]


class Bookstore(BaseHTTPRequestHandler):
    """Enough of the API for every tool in the document to succeed."""

    protocol_version = 'HTTP/1.1'
    server_version = 'bookstore-stand-in'

    def _handle(self) -> None:
        path = self.path.split('?', 1)[0]
        length = int(self.headers.get('content-length') or 0)
        raw = self.rfile.read(length) if length else b''
        body = json.loads(raw) if raw else None

        for method, pattern, answer in ROUTES:
            match = pattern.match(path)
            if method == self.command and match:
                status, payload = answer(match, body)
                self._send(status, payload)
                return
        self._send(404, {'error': f'no route for {self.command} {path}'})

    def _send(self, status: int, payload: Any) -> None:
        content = b'' if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if content:
            self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(content)))
        self.end_headers()
        if content:
            self.wfile.write(content)

    # `BaseHTTPRequestHandler` dispatches on `do_<METHOD>`, so these are its
    # names and not this file's.
    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _handle  # noqa: N815

    def log_message(self, fmt: str, *args: Any) -> None:
        # The point of the example: an MCP tool call arriving as HTTP.
        sys.stderr.write(f'  backend  {fmt % args}\n')


def start_backend() -> str:
    """Run the stand-in API on a free port, and return its base URL."""
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Bookstore)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.socket.getsockname()[:2]
    return f'http://{host}:{port}'


def build(base_url: str) -> FastMCP:
    """The MCP server for the document, calling the API at *base_url*."""
    return raml_mcp(
        SAMPLE,
        options=OPTIONS,
        base_uri_parameters=TENANT,
        client=httpx2.AsyncClient(base_url=base_url, timeout=10.0),
    )


def describe(server: Any) -> None:
    provider = next(p for p in server.providers if hasattr(p, 'dropped'))
    print(f'{server.name} -> {provider._client.base_url}\n')
    print('tools')
    for tool in sorted(provider._tools.values(), key=lambda t: t.name):
        print(f'  {tool.name:24} {", ".join(tool.parameters.get("properties", {}))}')
    print('\nresources')
    for uri in provider._resources:
        print(f'  {uri}')
    print('\nwhat MCP has no place for')
    for message in provider.dropped:
        print(f'  - {message}')


BACKEND = start_backend()
mcp = build(BACKEND)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stdio', action='store_true', help='serve over stdio, for an MCP client config')
    parser.add_argument('--describe', action='store_true', help='print what the document became, and exit')
    parser.add_argument('--port', type=int, default=8000)
    options = parser.parse_args()

    if options.describe:
        describe(mcp)
        raise SystemExit(0)

    if options.stdio:
        # Nothing may reach stdout: it carries the protocol.
        mcp.run()
    else:
        sys.stderr.write(f'{mcp.name}: MCP on port {options.port}, calling the bookstore at {BACKEND}\n')
        mcp.run(transport='http', port=options.port)
