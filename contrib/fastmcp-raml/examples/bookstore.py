"""Serve the Bookstore API as an MCP server.

The document is `fixtures/sample`, which is the project's worked example: a
templated `baseUri`, a `queryString` in place of `queryParameters`, two media
types under one body, a recursive type, four `documentation:` entries, and
security on every method. Run it to see what each of those becomes.

    uv run python examples/bookstore.py

Nothing is called: the API does not exist. What the server would send is decided
by the routes, and this prints them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib

from pyraml import ParseOptions, parse_from_path

from fastmcp_raml import RAMLProvider, description_of, title_of

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / 'fixtures'
SAMPLE = FIXTURES / 'sample' / 'api.raml'

# `workspace_root` because the document includes from a sibling directory, and
# the loader's sandbox is the entry file's own directory by default.
OPTIONS = ParseOptions(workspace_root=FIXTURES)

#: `baseUri` is `https://{tenant}.books.example.com/{version}`.
TENANT = {'tenant': 'acme'}


async def report() -> None:
    # `raml_mcp(SAMPLE, options=OPTIONS, base_uri_parameters=TENANT)` is the
    # one-liner. The provider is built on its own here so its `dropped` list and
    # its components can be read without a client session.
    raml = parse_from_path(SAMPLE, dataclasses.replace(OPTIONS, unwrap=True, validate=True))
    provider = RAMLProvider(
        raml,
        # `baseUri` is `https://{tenant}.books.example.com/{version}`. `{version}`
        # comes from the `version:` node; `{tenant}` is the caller's to supply,
        # and leaving it out is an error rather than an empty subdomain.
        base_uri_parameters=TENANT,
    )

    print(f'{title_of(raml)} -> {provider._client.base_url}\n')

    print('tools')
    for tool in sorted(await provider._list_tools(), key=lambda t: t.name):
        arguments = ', '.join(tool.parameters.get('properties', {}))
        print(f'  {tool.name:24} {arguments}')

    print('\nresources')
    for resource in await provider._list_resources():
        print(f'  {resource.uri}')

    # What `raml_mcp` would pass to `FastMCP(instructions=...)`.
    print('\ninstructions')
    print('  ' + (description_of(raml) or '').strip().splitlines()[0])

    print('\nwhat MCP has no place for')
    for message in provider.dropped:
        print(f'  - {message}')


if __name__ == '__main__':
    asyncio.run(report())
    # Serving it for real is the same call followed by `.run()`.
