"""`fastmcp-raml api.raml`: serve a RAML-described API as an MCP server.

    fastmcp-raml api.raml                           # stdio, requests to the document's baseUri
    fastmcp-raml api.raml --param tenant=acme       # bind a templated baseUri
    fastmcp-raml api.raml --base-url https://staging.example/v1 --header 'Authorization: Bearer ...'
    fastmcp-raml api.raml --mock                    # answered by raml-mock, in-process
    fastmcp-raml api.raml --http --port 8000        # streamable HTTP instead of stdio
    fastmcp-raml api.raml --describe                # what the document became, then exit

Stdio is the default because an MCP client's config starts a server that way.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path  # noqa: TC003 - typer reads these annotations at run time
from typing import TYPE_CHECKING, Annotated

import httpx2
import typer
from fastmcp import FastMCP
from fastmcp.server.lifespan import Lifespan, lifespan
from fastraml import ParseOptions, RamlError, parse_from_path

from fastmcp_raml.provider import DEFAULT_TIMEOUT, RAMLProvider
from fastmcp_raml.routes import base_url_of, description_of, title_of, unresolved_in

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from fastraml import Raml

app = typer.Typer(add_completion=False, help=__doc__)

#: Where a client points before something real is known: `--describe` sends
#: nothing, and `--mock` learns its port only once the mock is listening.
PENDING = 'http://pending.invalid'


def build(  # noqa: PLR0913 - one keyword per flag
    source: Path,
    *,
    base_url: str | None = None,
    parameters: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    workspace: Path | None = None,
    mock: bool = False,
    mock_config: Path | None = None,
    describing: bool = False,
) -> FastMCP:
    """The server the command line describes.

    One client carries every request, whatever says where they go: the
    document's `baseUri`, `--base-url`, or the mock. `--header` needs a client
    of its own, and `raml_mcp` builds one only when none is supplied, so this
    parses and assembles what `raml_mcp` would, around a client it owns.
    """
    raml = parse_from_path(source, ParseOptions(workspace_root=workspace, unwrap=True, validate=True))
    target = PENDING if mock or describing else base_url or _document_url(raml, parameters)
    client = httpx2.AsyncClient(base_url=target, headers=dict(headers or {}), timeout=DEFAULT_TIMEOUT)
    provider = RAMLProvider(raml, client=client, base_uri_parameters=parameters)

    serve = _mocked(source, workspace, mock_config, client) if mock else _closing(client)
    return FastMCP(
        name=title_of(raml) or 'RAML', instructions=description_of(raml), providers=[provider], lifespan=serve
    )


def _document_url(raml: Raml, parameters: Mapping[str, str] | None) -> str:
    """The document's `baseUri`, with every parameter bound, or a message naming the flag that fixes it."""
    url = base_url_of(raml, parameters)
    if url is None:
        msg = 'the document declares no baseUri: pass --base-url, or --mock'
        raise typer.BadParameter(msg, param_hint='SOURCE')
    missing = unresolved_in(url)
    if missing:
        flags = ' '.join(f'--param {name}=...' for name in missing)
        msg = f'the baseUri {url} leaves {", ".join(missing)} unbound: pass {flags}, --base-url, or --mock'
        raise typer.BadParameter(msg, param_hint='SOURCE')
    return url


def _closing(client: httpx2.AsyncClient) -> Lifespan:
    """A lifespan that closes the client with the server."""

    @lifespan
    async def serve(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
        async with client:
            yield {}

    return serve


def _mocked(source: Path, workspace: Path | None, config: Path | None, client: httpx2.AsyncClient) -> Lifespan:
    """A lifespan that runs `raml-mock` over the same document and points the client at it."""
    try:
        from raml_mock import MockOptions, mock_server  # noqa: PLC0415 - optional: fastmcp-raml[mock]
        from raml_mock.config_io import load_options  # noqa: PLC0415
    except ImportError as error:
        msg = '--mock needs raml-mock: install fastmcp-raml[mock]'
        raise typer.BadParameter(msg, param_hint='--mock') from error
    try:
        options = MockOptions() if config is None else load_options(config)
    except (OSError, TypeError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint='--mock-config') from error

    @lifespan
    async def serve(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
        parse = ParseOptions(workspace_root=workspace)
        async with mock_server(source, options=parse, mock_options=options) as backend, client:
            client.base_url = backend.url
            yield {'mock': backend}

    return serve


def describe(server: FastMCP) -> str:
    """What the document became: tools with their arguments, resources, and what MCP had no place for."""

    async def listed() -> tuple[list[str], list[str]]:
        tools = sorted(await server.list_tools(), key=lambda tool: tool.name)
        uris = [str(one.uri) for one in await server.list_resources()]
        uris += [one.uri_template for one in await server.list_resource_templates()]
        arguments = [', '.join(tool.parameters.get('properties', {})) for tool in tools]
        return [f'  {tool.name:24} {said}' for tool, said in zip(tools, arguments, strict=True)], [
            f'  {uri}' for uri in uris
        ]

    tools, resources = asyncio.run(listed())
    raml = [provider for provider in server.providers if isinstance(provider, RAMLProvider)]
    dropped = [f'  - {message}' for provider in raml for message in provider.dropped]
    lines = [server.name, '', 'tools', *tools, '', 'resources', *resources]
    if dropped:
        lines += ['', 'what MCP has no place for', *dropped]
    return '\n'.join(lines)


def _pairs(values: list[str], separator: str, flag: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for value in values:
        name, found, rest = value.partition(separator)
        if not found or not name.strip():
            msg = f'{value!r} is not NAME{separator}VALUE'
            raise typer.BadParameter(msg, param_hint=flag)
        pairs[name.strip()] = rest.strip()
    return pairs


@app.command()
def main(  # noqa: PLR0913, PLR0917 - CLI flags are intentionally flat
    source: Annotated[Path, typer.Argument(help='RAML API definition')],
    base_url: Annotated[str | None, typer.Option(help="where requests go; defaults to the document's baseUri")] = None,
    param: Annotated[list[str] | None, typer.Option(help='a baseUri parameter, NAME=VALUE; repeatable')] = None,
    header: Annotated[list[str] | None, typer.Option(help="sent with every request, 'Name: value'; repeatable")] = None,
    workspace: Annotated[
        Path | None, typer.Option(help='include workspace root; defaults to the file directory')
    ] = None,
    mock: Annotated[bool, typer.Option('--mock', help='answer from raml-mock over the same document')] = False,
    mock_config: Annotated[Path | None, typer.Option(help='raml-mock JSON configuration; implies --mock')] = None,
    http: Annotated[bool, typer.Option('--http', help='serve streamable HTTP instead of stdio')] = False,
    host: Annotated[str, typer.Option(help='HTTP listener address')] = '127.0.0.1',
    port: Annotated[int, typer.Option(help='HTTP listener port')] = 8000,
    describe_only: Annotated[bool, typer.Option('--describe', help='print what the document became, and exit')] = False,
) -> None:
    """Serve the API a RAML document describes as MCP tools and resources."""
    mock = mock or mock_config is not None
    if mock and base_url is not None:
        msg = '--mock answers requests itself; drop --base-url'
        raise typer.BadParameter(msg, param_hint='--base-url')
    try:
        server = build(
            source,
            base_url=base_url,
            parameters=_pairs(param or [], '=', '--param'),
            headers=_pairs(header or [], ':', '--header'),
            workspace=workspace,
            mock=mock,
            mock_config=mock_config,
            describing=describe_only,
        )
    except RamlError as error:
        typer.secho(f'{source}: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error

    if describe_only:
        typer.echo(describe(server))
        return
    if http:
        sys.stderr.write(f'{server.name}: MCP on http://{host}:{port}/mcp\n')
        server.run(transport='http', host=host, port=port)
    else:
        # Nothing may reach stdout: it carries the protocol.
        server.run(show_banner=False)


if __name__ == '__main__':
    app()
