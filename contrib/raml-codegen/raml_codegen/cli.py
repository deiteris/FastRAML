"""`raml-codegen <target> api.raml -o out/`.

A target is named `<language>-<library>`. `raml-codegen targets` lists them.

The input is a `fastraml tree` document, or the RAML itself when fastraml is
installed (`raml-codegen[raml]`), which runs the same projection in-process:

    raml-codegen python-httpx api.raml -o out/
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

SOURCE = typer.Argument(help='a .raml file, a `fastraml tree` JSON document, or - to read JSON from stdin')
OUTPUT = typer.Option('-o', '--output', help='directory to write the package into')
PACKAGE = typer.Option('--package', help="the generated package name; defaults to the API's title")
FORCE = typer.Option('--force', help='overwrite a starting-point file that is already there, such as impl.py')
WORKSPACE = typer.Option(
    '-w', '--workspace', help="include workspace root for .raml input; defaults to the file's directory"
)


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
    workspace: Annotated[pathlib.Path | None, WORKSPACE] = None,
) -> None:
    """Generate a typed httpx client that calls the documented API."""
    _run('python-httpx', source, output, package, force=force, workspace=workspace)


@app.command('python-fastapi')
def python_fastapi(
    source: Annotated[pathlib.Path, SOURCE],
    output: Annotated[pathlib.Path, OUTPUT],
    package: Annotated[str | None, PACKAGE] = None,
    force: Annotated[bool, FORCE] = False,
    workspace: Annotated[pathlib.Path | None, WORKSPACE] = None,
) -> None:
    """Generate a FastAPI server interface to implement."""
    _run('python-fastapi', source, output, package, force=force, workspace=workspace)


def _run(  # noqa: PLR0913 - one parameter per flag
    target: str,
    source: pathlib.Path,
    output: pathlib.Path,
    package: str | None,
    *,
    force: bool = False,
    workspace: pathlib.Path | None = None,
) -> None:
    try:
        generated = generate(_read(source, workspace), target, Settings(package=package))
    except UnreadableRaml as error:
        typer.secho(f'{source}: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error
    except UnreadableTree as error:
        typer.secho(f'{source}: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error
    except json.JSONDecodeError as error:
        typer.secho(f'{source}: not JSON: {error}', fg='red', err=True)
        raise typer.Exit(code=2) from error

    written = generated.write(output, force=force)
    typer.echo(f'wrote {len(written.paths)} files to {output.resolve()}')
    if written.kept:
        # Said rather than left to be discovered: the alternative to saying it
        # is the developer assuming the new stub arrived and wondering why the
        # method they were told about is not in the file.
        names = ', '.join(sorted(path.name for path in written.kept))
        typer.secho(
            f'kept {names} -- already there, and yours to edit.'
            ' Generate into an empty directory to see what the current ones'
            ' would say, or pass --force to overwrite them.',
            fg='yellow',
        )


class UnreadableRaml(Exception):
    """A `.raml` input that could not become a tree: no parser installed, or a document it rejected."""


def _read(source: pathlib.Path, workspace: pathlib.Path | None = None) -> object:
    """The tree, from a file, from stdin, or projected from RAML."""
    if str(source) == '-':
        return json.load(sys.stdin)
    if source.suffix.lower() == '.raml':
        return _project(source, workspace)
    return json.loads(source.read_text(encoding='utf-8'))


def _project(source: pathlib.Path, workspace: pathlib.Path | None) -> object:
    """What `fastraml tree -w WORKSPACE SOURCE` prints, without the process.

    The parser is optional, so this package still depends on the wire format
    and not on the model: the tree is serialised and read back exactly as the
    pipe would carry it, and nothing past this function sees a `Raml`. The
    options are the `tree` verb's -- unwrapped, not validated, so a document
    with a bad example still generates.
    """
    try:
        from fastraml import ParseOptions, RamlError, parse_from_path  # noqa: PLC0415 - optional: raml-codegen[raml]
        from fastraml.views.tree import build_tree  # noqa: PLC0415
    except ImportError as error:
        msg = 'reading RAML needs fastraml: install raml-codegen[raml], or pipe `fastraml tree` output in'
        raise UnreadableRaml(msg) from error
    try:
        raml = parse_from_path(source, ParseOptions(unwrap=True, validate=False, workspace_root=workspace))
    except RamlError as error:
        raise UnreadableRaml(str(error)) from error
    return json.loads(json.dumps(build_tree(raml)))


def main() -> None:
    sys.exit(app())


if __name__ == '__main__':
    main()
