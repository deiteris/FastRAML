"""Serve the Bookstore API as an MCP server, end to end.

The document is `fixtures/sample`, the repo's worked example: a templated
`baseUri`, a `queryString` in place of `queryParameters`, two media types under
one body, a recursive type, four `documentation:` entries, and security on every
method.

The API it describes does not exist, so `raml-mock` serves it from the same RAML
document for the MCP server's lifetime. Calling a tool builds a real HTTP request,
sends it over a loopback socket, and validates the RAML example or generated reply
against the schema the document declared.

    uv run python examples/bookstore.py              # HTTP, prints a URL
    uv run python examples/bookstore.py --stdio      # for an MCP client config
    uv run python examples/bookstore.py --describe    # what the document became

`fastmcp run examples/bookstore.py` works too: `mcp` is built at import.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import TYPE_CHECKING

import httpx2
from fastmcp.server.lifespan import lifespan
from fastraml import ParseOptions
from raml_mock import mock_server

from fastmcp_raml import RAMLProvider, raml_mcp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp import FastMCP

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / 'fixtures'
SAMPLE = FIXTURES / 'sample' / 'api.raml'

# `workspace_root` because the document includes from a sibling directory, and
# the loader's sandbox is the entry file's own directory by default.
OPTIONS = ParseOptions(workspace_root=FIXTURES)

TENANT = {'tenant': 'acme'}
_PENDING_MOCK_URL = 'http://raml-mock.invalid'


def build() -> FastMCP:
    """The MCP server, with its RAML mock bound to the same lifespan."""
    client = httpx2.AsyncClient(base_url=_PENDING_MOCK_URL, timeout=10.0)

    @lifespan
    async def mock_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
        async with mock_server(SAMPLE, options=OPTIONS) as backend, client:
            client.base_url = backend.url
            yield {'mock': backend}

    return raml_mcp(
        SAMPLE,
        options=OPTIONS,
        base_uri_parameters=TENANT,
        client=client,
        lifespan=mock_lifespan,
    )


def describe(server: FastMCP) -> None:
    provider = next(p for p in server.providers if isinstance(p, RAMLProvider))
    print(f'{server.name} -> raml-mock over {SAMPLE.name}\n')
    print('tools')
    for tool in sorted(provider._tools.values(), key=lambda t: t.name):
        print(f'  {tool.name:24} {", ".join(tool.parameters.get("properties", {}))}')
    print('\nresources')
    for uri in provider._resources:
        print(f'  {uri}')
    print('\nwhat MCP has no place for')
    for message in provider.dropped:
        print(f'  - {message}')


mcp = build()


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
        sys.stderr.write(f'{mcp.name}: MCP on port {options.port}, backed by raml-mock\n')
        mcp.run(transport='http', port=options.port)
