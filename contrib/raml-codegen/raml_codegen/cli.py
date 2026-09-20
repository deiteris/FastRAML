"""`raml-codegen <target> api.json -o out/`.

A target is named `<language>-<library>`. `raml-codegen targets` lists them.

The input is a `fastraml tree` document. Produce one where the RAML lives:

    fastraml tree api.raml > api.json
    fastraml tree api.raml | raml-codegen python-httpx - -o out/
"""

from __future__ import annotations

import json
import pathlib  # noqa: TC003 - typer reads these annotations at run time
import sys
from typing import Annotated

import typer

from .reader import UnreadableTree
from .targets import TARGETS, Settings, generate

app = typer.Typer(add_completion=False, help=__doc__)

SOURCE = typer.Argument(help='a `fastraml tree` JSON document, or - to read one from stdin')
OUTPUT = typer.Option('-o', '--output', help='directory to write the package into')
PACKAGE = typer.Option('--package', help="the generated package name; defaults to the API's title")
FORCE = typer.Option('--force', help='overwrite a starting-point file that is already there, such as impl.py')


@app.command('targets')
def list_targets() -> None:
    """List what this can generate."""
    for name in sorted(TARGETS):
        typer.echo(name)


@app.command('python-httpx')
def python_httpx(
    source: Annotated[pathlib.Path, SOURCE],
    output: Annotated[pathlib.Path, OUTPUT],
    package: Annotated[str | None, PACKAGE] = None,
    force: Annotated[bool, FORCE] = False,
) -> None:
    """Generate a typed httpx client that calls the documented API."""
    _run('python-httpx', source, output, package, force=force)


@app.command('python-fastapi')
def python_fastapi(
    source: Annotated[pathlib.Path, SOURCE],
    output: Annotated[pathlib.Path, OUTPUT],
    package: Annotated[str | None, PACKAGE] = None,
    force: Annotated[bool, FORCE] = False,
) -> None:
    """Generate a FastAPI server interface to implement."""
    _run('python-fastapi', source, output, package, force=force)


def _run(
    target: str,
    source: pathlib.Path,
    output: pathlib.Path,
    package: str | None,
    *,
    force: bool = False,
) -> None:
    try:
        generated = generate(_read(source), target, Settings(package=package))
    except UnreadableTree as error:
        typer.secho(f'{source}: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error
    except json.JSONDecodeError as error:
        typer.secho(f'{source}: not JSON: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error

    written = generated.write(output, force=force)
    typer.echo(f'wrote {len(written.paths)} files to {output.resolve()}')
    for path in written.kept:
        # Said rather than left to be discovered: the alternative to saying it
        # is the developer assuming the new stub arrived and wondering why the
        # method they were told about is not in the file.
        typer.secho(
            f'kept {path.name}, which already exists and is yours to edit'
            ' -- generate into an empty directory to see the current stub,'
            ' or pass --force to overwrite it',
            fg='yellow',
        )


def _read(source: pathlib.Path) -> object:
    """The tree, from a file or from stdin."""
    if str(source) == '-':
        return json.load(sys.stdin)
    return json.loads(source.read_text(encoding='utf-8'))


def main() -> None:
    sys.exit(app())


if __name__ == '__main__':
    main()
