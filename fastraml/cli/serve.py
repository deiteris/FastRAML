"""The long-running verbs: `serve` (the viewer) and `lsp` (the language
server). Each imports its optional extra inside the verb.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, fail, parse_or_report, remote_client

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
        return fail('serve needs the viewer: install fastraml-viewer (fastraml[serve])')

    try:
        server = serve_viewer(build_tree(raml), host=args.host, port=args.port)
    except OSError as err:
        return fail(f'serve: {err}')
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
        return fail('lsp needs pygls: pip install "fastraml[lsp]"')
    from fastraml.gctuning import tuned_gc  # noqa: PLC0415

    try:
        server = RamlServer(args.fastraml_config, remote_client(args))
    except ValueError as err:
        return fail(f'lint config: {err}')
    with tuned_gc():
        server.start_io()
    return EXIT_OK
