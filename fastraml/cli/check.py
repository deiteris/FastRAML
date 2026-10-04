"""`validate` and `info`: whether a document parses, and what it cost."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, parse_options, report_invalid

if TYPE_CHECKING:
    import argparse

    from fastraml.errors import RamlError
    from fastraml.registry import Raml


def _validate(args: argparse.Namespace) -> int:
    import time  # noqa: PLC0415 - version and catalogue commands do not parse

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415

    options = parse_options(args)
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
            import json  # noqa: PLC0415 - only JSON output needs the encoder

            record = {'path': path, 'valid': error is None, 'error': error.to_dict() if error else None}
            print(json.dumps(record))
            continue

        if error is not None:
            # Flush stdout first so redirected streams keep per-file order.
            sys.stdout.flush()
            report_invalid(path, error)
            sys.stderr.flush()
        elif args.verbose:
            print(f'{path}: valid ({elapsed:.1f} ms)')
        if args.verbose > 1 and raml is not None:
            _report(raml, elapsed)
    return EXIT_INVALID if failed else EXIT_OK


def _info(args: argparse.Namespace) -> int:
    import time  # noqa: PLC0415 - version and catalogue commands do not parse

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = args.files[0]
    started = time.perf_counter()
    try:
        raml = parse_from_path(path, parse_options(args))
    except RamlError as err:
        report_invalid(path, err)
        return EXIT_INVALID
    _report(raml, (time.perf_counter() - started) * 1e3, path=path)
    return EXIT_OK


def _report(raml: Raml, elapsed: float, *, path: str | None = None) -> None:
    """The counts a user reaches for when asking why a parse was slow or wrong."""
    from fastraml.yamlnode import backend_name  # noqa: PLC0415 - reporting only

    rows = [] if path is None else [('file', path)]
    rows += [
        # The two YAML backends do not accept quite the same documents
        # (docs/01 § 4.4).
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
