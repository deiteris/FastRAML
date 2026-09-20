"""`raml-codegen <target> API.raml -o out/`."""

from __future__ import annotations

import json
import pathlib  # noqa: TC003 - typer reads these annotations at run time
import sys
from typing import Annotated

import typer

from .reader import UnreadableTree
from .targets import TARGETS, Settings, generate, generate_from_path

app = typer.Typer(add_completion=False, help=__doc__)


@app.command('targets')
def list_targets() -> None:
    """What this can generate."""
    for name in sorted(TARGETS):
        typer.echo(name)


@app.command('python')
def python_target(
    source: Annotated[pathlib.Path, typer.Argument(help='a RAML file, or a `fastraml tree` JSON document')],
    output: Annotated[pathlib.Path, typer.Option('-o', '--output', help='directory to write the package into')],
    workspace_root: Annotated[
        pathlib.Path | None,
        typer.Option('-w', '--workspace-root', help='the root includes may not ascend past'),
    ] = None,
    package: Annotated[
        str | None,
        typer.Option('--package', help="the generated package name; defaults to the API's title"),
    ] = None,
) -> None:
    """Generate a typed httpx client."""
    _run('python', source, output, workspace_root, package)


def _run(
    target: str,
    source: pathlib.Path,
    output: pathlib.Path,
    workspace_root: pathlib.Path | None,
    package: str | None,
) -> None:
    settings = Settings(package=package)
    try:
        # A `.json` source is a tree somebody already projected -- which is the
        # point of a wire format, and the only way to generate on a machine that
        # does not have the RAML files.
        if source.suffix == '.json':
            generated = generate(json.loads(source.read_text(encoding='utf-8')), target, settings)
        else:
            generated = generate_from_path(source, target, settings, workspace_root=workspace_root)
    except UnreadableTree as error:
        typer.secho(f'{source}: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error

    written = generated.write(output)
    typer.echo(f'wrote {len(written)} files to {output.resolve()}')


def main() -> None:
    sys.exit(app())


if __name__ == '__main__':
    main()
