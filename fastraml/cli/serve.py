"""The long-running verbs: `serve` (the viewer) and `lsp` (the language
server). Each imports its optional extra inside the verb.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, new_http_client, parse_or_report

if TYPE_CHECKING:
    import argparse


def _serve(args: argparse.Namespace) -> int:
    """The document in a browser: the `tree` projection, served as `api.json`.

    Parses as `tree` does, without validation. `fastraml_viewer.serve` routes
    `api.json` to this document ahead of the bundle's own sample file.
    `fastraml_viewer` is an optional extra imported here only.
    """
    raml = parse_or_report(args)
    if raml is None:
        return EXIT_INVALID

    from fastraml.views.tree import build_tree  # noqa: PLC0415 - serve verb only

    try:
        from fastraml_viewer import serve as serve_viewer  # noqa: PLC0415 - optional: fastraml[serve]
    except ImportError:
        print('serve needs the viewer: install fastraml-viewer (fastraml[serve])', file=sys.stderr)
        return EXIT_INVALID

    try:
        server = serve_viewer(build_tree(raml), host=args.host, port=args.port)
    except OSError as err:
        print(f'serve: {err}', file=sys.stderr)
        return EXIT_INVALID
    print(f'viewer: http://{args.host}:{server.server_address[1]}/  (Ctrl-C to stop)', file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def _lsp(args: argparse.Namespace) -> int:
    """The language server (docs/21 § 5), deferring full collections for its
    whole run (docs/12 § 6). pygls is an optional extra imported here only.
    """
    try:
        from fastraml.service.lsp import RamlServer  # noqa: PLC0415 - optional: fastraml[lsp]
    except ImportError:
        print('lsp needs pygls: pip install "fastraml[lsp]"', file=sys.stderr)
        return EXIT_INVALID
    from fastraml.gctuning import tuned_gc  # noqa: PLC0415

    config = args.fastraml_config
    http_client = new_http_client() if args.remote or config.parser.remote else None
    try:
        server = RamlServer(config, http_client)
    except ValueError as err:
        print(f'lint config: {err}', file=sys.stderr)
        return EXIT_INVALID
    with tuned_gc():
        server.start_io()
    return EXIT_OK
