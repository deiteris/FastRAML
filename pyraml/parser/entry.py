"""Entry points and the pass driver.

`parse_from_path` and `parse_from_string` differ only in where the first bytes
come from; both then run the same fixed sequence of passes over one `Raml`.
Phase 1 implements P0 to P3. The later passes are named and left as no-ops so
the order they run in is settled here rather than being rediscovered.

See docs/02-architecture.md section 1 and docs/13-public-api.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pyraml.errors import ErrorKind, RamlError
from pyraml.loaders import build_loader
from pyraml.parser.fragments import decode_fragment, identify_fragment
from pyraml.registry import DEFAULT_MAX_INCLUDE_SIZE, Raml
from pyraml.types.resolve import resolve_shapes
from pyraml.types.unwrap import unwrap_shapes
from pyraml.uris import path_to_file_uri
from pyraml.yamlnode import decode_source, read_head

if TYPE_CHECKING:
    from pyraml.loaders import ResourceLoader

__all__ = [
    'ParseOptions',
    'parse_from_path',
    'parse_from_string',
]


@dataclass(frozen=True, slots=True)
class ParseOptions:
    """How one parse should behave.

    Pass `unwrap=True, validate=True` together unless you specifically need to
    inspect un-flattened declarations: `validate=True` on its own has to unwrap
    a private copy of every type it checks.
    """

    unwrap: bool = False
    validate: bool = False
    retain_source: bool = False
    workspace_root: str | os.PathLike[str] | None = None
    max_include_size: int = DEFAULT_MAX_INCLUDE_SIZE
    #: Replaces the sandboxed `file://` loader. Supplying one makes the caller
    #: responsible for path safety (docs/03 section 5).
    file_loader: ResourceLoader | None = None
    #: Supply a client to enable `http(s)` includes; without one they are refused.
    http_client: Any | None = None
    regex_engine: Literal['re', 're2'] = 're'
    max_type_depth: int = 200


_DEFAULT_OPTIONS = ParseOptions()


def parse_from_path(path: str | os.PathLike[str], options: ParseOptions | None = None) -> Raml:
    """Parse the RAML document at `path`.

    A relative path resolves against the current directory, and the workspace
    root defaults to the file's own directory — which is both the I/O sandbox
    and the base for RAML-absolute includes.
    """
    options = options or _DEFAULT_OPTIONS
    entry = Path(path)
    if not entry.is_absolute():
        entry = Path.cwd() / entry
    raml = _new_registry(options, default_root=str(entry.parent))

    uri = path_to_file_uri(entry)
    try:
        text = decode_source(raml.loader.load(uri))
    except OSError as err:
        raise RamlError.wrap('load resource', err, uri, kind=ErrorKind.READING) from err
    return _parse(raml, uri, text, options)


def parse_from_string(
    content: str,
    *,
    file_name: str,
    base_dir: str | os.PathLike[str],
    options: ParseOptions | None = None,
) -> Raml:
    """Parse `content` as though it had been read from `base_dir/file_name`.

    `base_dir` must be absolute: a relative `!include` has to resolve against
    something real.
    """
    options = options or _DEFAULT_OPTIONS
    root = Path(base_dir)
    if not root.is_absolute():
        raise RamlError.new('base_dir must be an absolute path', str(root), kind=ErrorKind.READING)

    raml = _new_registry(options, default_root=str(root))
    return _parse(raml, path_to_file_uri(root / file_name), content, options)


def _new_registry(options: ParseOptions, *, default_root: str) -> Raml:
    workspace_root = os.fspath(options.workspace_root) if options.workspace_root is not None else default_root
    return Raml(
        loader=build_loader(workspace_root, file_loader=options.file_loader, http_client=options.http_client),
        workspace_root_uri=path_to_file_uri(workspace_root),
        max_include_size=options.max_include_size,
        retain_source=options.retain_source,
        regex_engine=options.regex_engine,
    )


def _parse(raml: Raml, uri: str, text: str, options: ParseOptions) -> Raml:
    """The pass driver. Each step's precondition is the previous step's result."""
    # P0 — identify the fragment kind from the first line. Fails fast: a
    # document with no recognised header is not RAML.
    head = read_head(text)
    kind = identify_fragment(head)
    if kind is None:
        raise RamlError.new('unknown fragment kind', uri, info={'head': head}, kind=ErrorKind.PARSING)

    # P1 to P3 — compose, decode, and resolve `uses:` recursively. All three
    # happen inside decode_fragment, which owns their ordering.
    raml.entry_point = decode_fragment(raml, uri, kind, text)

    # P4-P6 — endpoints, security schemes, URI parameter propagation. API only.
    # Phases 5 to 7; the order is fixed by docs/02 section 1.

    # P7 — drain the unknown worklist: every declaration whose kind the document
    # alone could not settle now gets one. After this, invariant I5 holds.
    resolve_shapes(raml)

    # P8 — resolve domain extensions against their annotation types. Phase 7.

    # P9 — flatten every inheritance chain, then mark the cycles. Opt-in: the
    # un-flattened model is what a formatter or a doc generator wants.
    if options.unwrap:
        unwrap_shapes(raml, max_depth=options.max_type_depth)

    # P10   — validate, when options.validate. Phase 8.
    return raml
