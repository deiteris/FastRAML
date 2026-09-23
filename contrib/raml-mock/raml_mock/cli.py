"""`raml-mock serve`: run a mock server for a RAML file."""

from __future__ import annotations

import dataclasses
from pathlib import Path  # noqa: TC003 - Typer resolves annotations at runtime
from typing import Annotated

import typer
from aiohttp import web
from fastraml import ParseOptions

from raml_mock import MockOptions, create_app
from raml_mock.config_io import load_options

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def serve(  # noqa: PLR0913, PLR0917 - CLI flags are intentionally flat
    source: Annotated[Path, typer.Argument(help='RAML API definition')],
    host: Annotated[str, typer.Option(help='Listener address')] = '127.0.0.1',
    port: Annotated[int, typer.Option(help='Listener port')] = 8080,
    workspace: Annotated[Path | None, typer.Option(help='Include workspace root')] = None,
    config: Annotated[Path | None, typer.Option(help='JSON mock behavior configuration')] = None,
    seed: Annotated[str | None, typer.Option(help='Deterministic generation seed')] = None,
    collection_size: Annotated[int | None, typer.Option(help='Generated collection size')] = None,
) -> None:
    """Run a RAML-backed HTTP mock."""
    try:
        options = MockOptions() if config is None else load_options(config)
    except (OSError, TypeError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint='--config') from error
    if seed is not None or collection_size is not None:
        generation = dataclasses.replace(
            options.generation,
            seed=options.generation.seed if seed is None else seed,
            collection_size=options.generation.collection_size if collection_size is None else collection_size,
        )
        options = dataclasses.replace(options, generation=generation)
    parse_options = ParseOptions(workspace_root=workspace) if workspace is not None else None
    try:
        application = create_app(source, options=parse_options, mock_options=options)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint='--config') from error
    web.run_app(application, host=host, port=port, handler_cancellation=True)


if __name__ == '__main__':
    app()
