"""A rendered document, parsed back and projected -- what every integration serves.

    report --to_raml()--> RAML text --parse_from_string()--> Raml --build_tree()--> tree

**The parse is not a formality.** It runs with `validate=True`, so a document
that will not parse, or whose examples do not validate, raises `BuildError`
here rather than reaching a client. It is also the only route to the tree:
`build_tree` projects a parsed `Raml`, so nothing reaches a viewer without
going through RAML text first.

Framework-free, so `fastapi-raml` and `aiohttp-raml` share one pipeline and
one error, and differ only in how they route and cache. Needs `fastraml`,
the `serve` extra: the document model itself does not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastraml import ParseOptions, RamlError, build_tree, parse_from_string

if TYPE_CHECKING:
    from raml_document.report import Report

__all__ = ['RAML_MEDIA_TYPE', 'BuildError', 'Served', 'build', 'media_type']

#: RAML's registered media type (spec § Introduction).
RAML_MEDIA_TYPE = 'application/raml+yaml'

logger = logging.getLogger('raml_document')

#: The name the rendered document is parsed under, and the one an error cites.
_FILE_NAME = 'api.raml'
#: Where a relative `!include` would resolve. Nothing rendered writes one, so
#: nothing is read from it; it only has to be an absolute path.
_BASE_DIR = Path(__file__).resolve().parent


class BuildError(Exception):
    """The rendered RAML does not parse, or, under `strict`, something was left out.

    `str()` cites each problem at its line in the rendered text and quotes that
    line: the text is not served while it fails, so the error is the only place
    a reader sees it. `text` holds all of it.
    """

    def __init__(self, message: str, text: str, dropped: list[str]) -> None:
        super().__init__(message)
        #: The RAML that was rendered.
        self.text = text
        #: `Report.dropped` for the render.
        self.dropped = dropped


@dataclass(slots=True)
class Served:
    """One document, rendered and parsed back."""

    #: The RAML source.
    text: str
    #: `fastraml tree` output for it, ready for `json.dumps`.
    tree: Any
    #: Everything the renderer could not express (`Report.dropped`).
    dropped: list[str]
    #: `tree` as JSON bytes, encoded once per build.
    tree_json: bytes = field(repr=False, default=b'')


def build(report: Report, *, strict: bool = False, log: logging.Logger = logger) -> Served:
    """Parse `report`'s document back and project the tree.

    Raises `BuildError` if the document does not parse -- or, with
    `strict=True`, if the renderer had to leave anything out. Each entry of
    `report.dropped` is logged as a warning on `log`, so what the document
    leaves out is said where its owner looks.

    `validate=True` also checks every example, and `unwrap=True` is required by
    the tree view, which is the effective document rather than the declared one.
    """
    text = report.to_raml()
    title = report.document.title
    for entry in report.dropped:
        log.warning('RAML for %r leaves out %s', title, entry)
    if strict and report.dropped:
        listed = '\n'.join(f'  {entry}' for entry in report.dropped)
        raise BuildError(f'the RAML rendered for {title!r} leaves out:\n{listed}', text, report.dropped)
    try:
        raml = parse_from_string(
            text,
            file_name=_FILE_NAME,
            base_dir=_BASE_DIR,
            options=ParseOptions(unwrap=True, validate=True),
        )
    except RamlError as error:
        raise BuildError(_explain(title, error, text), text, report.dropped) from error
    tree = build_tree(raml)
    return Served(text=text, tree=tree, dropped=report.dropped, tree_json=json.dumps(tree).encode())


def _explain(title: str, error: RamlError, text: str) -> str:
    """Each problem's innermost frame, at its line in `text`, with that line quoted."""
    lines = text.splitlines()
    out = [f'the RAML rendered for {title!r} does not parse:']
    for chain in error.chains():
        frame = chain[-1]
        where = f'{_FILE_NAME}:{frame.position}' if frame.position is not None else _FILE_NAME
        out.append(f'  {where} {frame.rendered_message()}')
        if frame.position is not None and 0 < frame.position.line <= len(lines):
            out.append(f'      {frame.position.line} | {lines[frame.position.line - 1]}')
    return '\n'.join(out)


def media_type(accept: str) -> str:
    """What to answer a request for the RAML source with, given its `Accept` header.

    `application/raml+yaml`, unless a browser is asking: it sends `text/html`
    and names no RAML type, and given a type it cannot show it downloads the
    document instead of showing it. Plain text is the same bytes, displayed.
    """
    if 'text/html' in accept and RAML_MEDIA_TYPE not in accept:
        return 'text/plain'
    return RAML_MEDIA_TYPE
