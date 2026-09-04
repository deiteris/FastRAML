"""The `pyraml` console script — docs/13-public-api.md section 8.

```
pyraml validate [-w ROOT] [--no-workspace-guard] [-r] [-v] [--json] FILE...
pyraml info [-w ROOT] [-r] FILE
```

Mirrors the reference implementation's `raml` tool closely enough that the two
can be diffed fixture by fixture (docs/14-testing.md section 1.3), which is why
`--json` emits the same trace-chain shape `RamlError.to_dict()` produces and why
`validate` keeps going after a failing file rather than stopping at it.

Everything here is presentation. No parsing rule lives in this module: it turns
arguments into a `ParseOptions`, calls an entry point, and formats what comes
back.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import TYPE_CHECKING, Any

from pyraml import __version__
from pyraml.errors import RamlError
from pyraml.loaders import FileLoader
from pyraml.parser.entry import ParseOptions, parse_from_path
from pyraml.yamlnode import backend_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyraml.registry import Raml

__all__ = ['main']

EXIT_OK = 0
EXIT_INVALID = 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == 'info':
        return _info(args)
    return _validate(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='pyraml', description='Parse and validate RAML 1.0.')
    parser.add_argument('--version', action='version', version=f'pyraml {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)

    validate = commands.add_parser('validate', help='parse, unwrap and validate one or more files')
    validate.add_argument('files', metavar='FILE', nargs='+')
    validate.add_argument('--json', action='store_true', help='emit one JSON object per file (JSON Lines)')
    validate.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help='report each file and its timing; twice for the backend and model counts',
    )
    _add_common(validate)

    info = commands.add_parser('info', help='backend, timings and model counts for one file')
    info.add_argument('files', metavar='FILE', nargs=1)
    _add_common(info)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('-w', '--workspace-root', metavar='ROOT', help='confine file reads to this directory')
    parser.add_argument(
        '--no-workspace-guard',
        action='store_true',
        help='read any path the process can reach; disables the sandbox',
    )
    parser.add_argument('-r', '--remote', action='store_true', help='allow http(s) includes')


# -- validate -----------------------------------------------------------------


def _validate(args: argparse.Namespace) -> int:
    options = _options(args)
    failed = False
    for path in args.files:
        started = time.perf_counter()
        error: RamlError | None = None
        try:
            raml = parse_from_path(path, options)
        except RamlError as err:
            error, raml = err, None
        elapsed = (time.perf_counter() - started) * 1e3
        failed = failed or error is not None

        if args.json:
            record = {'path': path, 'valid': error is None, 'error': error.to_dict() if error else None}
            print(json.dumps(record))
            continue

        if error is not None:
            # Flushed first, or the two streams interleave by buffer rather than
            # by file once either is redirected, and a multi-file run reports
            # the failures before the successes that preceded them.
            sys.stdout.flush()
            print(f'{path}: invalid', file=sys.stderr)
            print(error, file=sys.stderr)
            sys.stderr.flush()
        elif args.verbose:
            print(f'{path}: valid ({elapsed:.1f} ms)')
        if args.verbose > 1 and raml is not None:
            _report(raml, elapsed)
    return EXIT_INVALID if failed else EXIT_OK


# -- info ---------------------------------------------------------------------


def _info(args: argparse.Namespace) -> int:
    path = args.files[0]
    started = time.perf_counter()
    try:
        raml = parse_from_path(path, _options(args))
    except RamlError as err:
        print(f'{path}: invalid', file=sys.stderr)
        print(err, file=sys.stderr)
        return EXIT_INVALID
    _report(raml, (time.perf_counter() - started) * 1e3, path=path)
    return EXIT_OK


def _report(raml: Raml, elapsed: float, *, path: str | None = None) -> None:
    """The counts a user reaches for when asking why a parse was slow or wrong."""
    rows = [] if path is None else [('file', path)]
    rows += [
        # Semantic as well as diagnostic: the two YAML backends do not accept
        # quite the same documents (docs/01 deviation D9).
        ('backend', backend_name()),
        ('elapsed', f'{elapsed:.1f} ms'),
        ('fragments', str(len(raml.fragments))),
        ('types', str(sum(len(shapes) for shapes in raml.fragment_typedefs.values()))),
        ('shapes', str(len(raml.shapes))),
        ('endpoints', str(len(raml.endpoints))),
        ('annotations', str(len(raml.domain_extensions))),
    ]
    for name, value in rows:
        print(f'{name:<12} {value}')


# -- options ------------------------------------------------------------------


def _options(args: argparse.Namespace) -> ParseOptions:
    """`unwrap` and `validate` are always on: the CLI's job is to find faults."""
    return ParseOptions(
        unwrap=True,
        validate=True,
        workspace_root=args.workspace_root,
        file_loader=FileLoader() if args.no_workspace_guard else None,
        http_client=_http_client() if args.remote else None,
    )


def _http_client() -> Any:
    """A client for `-r`, from whichever of the two usual libraries is installed.

    pyRAML depends on neither — `HTTPLoader` duck-types `get(url)` — so the CLI
    is where one has to be produced, and where a user who asked for remote
    includes without a client gets told so.
    """
    for module_name, factory in (('httpx', 'Client'), ('requests', 'Session')):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        return getattr(module, factory)()
    message = '--remote needs an HTTP client: install httpx or requests'
    raise SystemExit(message)


if __name__ == '__main__':
    sys.exit(main())
