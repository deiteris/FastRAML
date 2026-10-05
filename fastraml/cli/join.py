"""`join`: API documents combined into one (docs/20)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.common import emit_document, fail, parse_options

if TYPE_CHECKING:
    import argparse


def _join(args: argparse.Namespace) -> int:
    """Combine API documents into one (docs/20)."""
    from pathlib import Path  # noqa: PLC0415 - join only

    import yaml  # noqa: PLC0415 - config parameters only

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.join import BaseUriOverride, JoinOptions, join  # noqa: PLC0415
    from fastraml.uris import path_to_file_uri  # noqa: PLC0415
    from fastraml.yamlnode import compose  # noqa: PLC0415

    if len(args.files) < 2:  # noqa: PLR2004 - a join of one document is a copy
        return fail('join: at least two INPUT files are required')

    def uri_of(path: str) -> str:
        return path_to_file_uri(Path(path).absolute())

    configured = args.fastraml_config.join
    overrides: dict[str, BaseUriOverride] = {}
    for item in configured.inputs:
        if item.base_uri is None:
            if item.base_uri_parameters is not None:
                return fail(f'join: {item.path}: baseUriParameters needs a baseUri beside it')
            continue
        parameters = None
        if item.base_uri_parameters is not None:
            text = yaml.safe_dump(dict(item.base_uri_parameters), sort_keys=False, allow_unicode=True)
            parameters = compose(text, uri=path_to_file_uri(Path(args.config).absolute()))
        overrides[uri_of(item.path)] = BaseUriOverride(item.base_uri, parameters)
    # A CLI override replaces the configuration's entry for that input whole.
    for raw in args.base_uri:
        path, separator, uri = raw.partition('=')
        if not separator or not path or not uri:
            return fail(f'join: --base-uri takes INPUT=URI, not {raw!r}')
        overrides[uri_of(path)] = BaseUriOverride(uri)

    options = JoinOptions(
        title=args.title if args.title is not None else configured.title,
        version=args.api_version if args.api_version is not None else configured.version,
        description=args.description if args.description is not None else configured.description,
        base_uris=overrides,
        output=args.output,
        parse=parse_options(args, validate=False),
    )
    try:
        text = join(args.files, options)
    except RamlError as err:
        print('join: failed', file=sys.stderr)
        return fail(err)
    return emit_document(args, text)
