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
from typing import TYPE_CHECKING, Any, Final, Literal

from fastraml.errors import ErrorKind, RamlError
from fastraml.loaders import build_loader
from fastraml.parser.annotations import resolve_domain_extensions
from fastraml.parser.endpoint_build import build_endpoints
from fastraml.parser.fragments import decode_fragment, identify_fragment
from fastraml.parser.security import apply_security_schemes
from fastraml.registry import DEFAULT_MAX_INCLUDE_SIZE, Raml
from fastraml.types.resolve import resolve_shapes
from fastraml.types.unwrap import unwrap_shapes
from fastraml.types.validate import check_declared_discriminators, validate_shapes
from fastraml.uris import path_to_file_uri
from fastraml.yamlnode import DEFAULT_MAX_DEPTH, decode_source, read_head

if TYPE_CHECKING:
    from fastraml.loaders import ResourceLoader

__all__ = [
    'ParseOptions',
    'parse_from_path',
    'parse_from_string',
    'parse_lenient',
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
    #: One ceiling for every recursive descent bounded only by the input — the
    #: document conversion in P0, unwrap and recursion-marking in P9, the walks
    #: in P10, and the JSON Schema walks. They defend the same C stack, so one
    #: number governs them all (docs/12-performance.md section 14).
    max_depth: int = DEFAULT_MAX_DEPTH


_DEFAULT_OPTIONS = ParseOptions()


def parse_from_path(path: str | os.PathLike[str], options: ParseOptions | None = None) -> Raml:
    """Parse the RAML document at `path`.

    A relative path resolves against the current directory, and the workspace
    root defaults to the file's own directory — which is both the I/O sandbox
    and the base for RAML-absolute includes.
    """
    options = options or _DEFAULT_OPTIONS
    return _parse(*_open(path, options), options)


def _open(path: str | os.PathLike[str], options: ParseOptions) -> tuple[Raml, str, str]:
    """Build the registry and read the entry file. Raises in both modes."""
    entry = Path(path)
    if not entry.is_absolute():
        entry = Path.cwd() / entry
    raml = _new_registry(options, default_root=str(entry.parent))

    uri = path_to_file_uri(entry)
    try:
        text = decode_source(raml.loader.load(uri))
    except OSError as err:
        raise RamlError.wrap('load resource', err, uri, kind=ErrorKind.READING) from err
    return raml, uri, text


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
    # Absolute: `path_to_file_uri` has nothing to resolve a relative path
    # against, and `SafeFileLoader` absolutises its own copy, so anything less
    # names a different directory from the one reads are confined to.
    #
    # `abspath` and not `Path.resolve()`: the loader keeps `root` and
    # `_real_root` apart on purpose and this names the first of the two.
    # Resolving symlinks here would name a directory the loader never compares
    # a lexical path against.
    given = options.workspace_root
    workspace_root = os.path.abspath(os.fspath(given)) if given is not None else default_root  # noqa: PTH100 - see above
    return Raml(
        loader=build_loader(workspace_root, file_loader=options.file_loader, http_client=options.http_client),
        workspace_root_uri=path_to_file_uri(workspace_root),
        max_include_size=options.max_include_size,
        retain_source=options.retain_source,
        regex_engine=options.regex_engine,
        max_depth=options.max_depth,
    )


def parse_lenient(path: str | os.PathLike[str], options: ParseOptions | None = None) -> tuple[Raml, RamlError | None]:
    """Parse `path`, returning the partial model **and** the error, never raising.

    What an editor integration wants: a document with a mistake in it should
    still yield the fragments, the endpoints, the types that were fine and their
    positions, so that completion and go-to-definition keep working while the
    author is mid-edit.

    A thin wrapper. It runs the same passes in the same order and stops where a
    strict parse stops; the difference is that it hands back the half-built
    `Raml` instead of dropping it. Every pass already accumulates internally, so
    the error returned is the same one `parse_from_path` would have raised —
    complete for the pass that failed, at the granularity docs/11 § 2 gives.

    **Continuing past the failing pass was tried, measured, and rejected.** The
    passes consume each other's output, so a later pass walking state an earlier
    one reported as broken re-derives the same fault instead of finding a new
    one. Measured: a missing library used by twenty types goes from **1
    diagnostic to 41**, and a single dangling type name doubles, because P7
    re-reports what P1-P3 said and P9 re-reports P7. Recovering the genuinely
    independent diagnostics means skipping the *entities* known to be broken
    rather than the passes, which is real machinery inside P9 and P10; it is
    recorded as an After-v1 item in docs/15 rather than approximated here.

    Four failures still raise, because none of them leaves anything to hand back
    (`_FATAL`, and docs/13-public-api.md § 1): an unreadable entry file, a
    missing or unrecognised RAML header, a root that is not a mapping, and a
    fragment whose kind does not match its context.
    """
    options = options or _DEFAULT_OPTIONS
    raml, uri, text = _open(path, options)
    try:
        _parse(raml, uri, text, options)
    except RamlError as err:
        if err.head.message in _FATAL:
            raise
        # P1-P3 failing leaves `entry_point` unassigned, but `decode_fragment`
        # registers the fragment before decoding its body — so a document whose
        # `uses:` or whose type declarations failed still has a partial one to
        # hand back, which is the commonest state an editor sees.
        if raml.entry_point is None:
            raml.entry_point = raml.get_fragment(uri)
        return raml, err
    return raml, None


#: The failures `parse_lenient` re-raises. Each leaves either no model at all or
#: an empty shell that would misrepresent the file more than an exception does.
#:
#: Matched on the **head** of the error, which is where all four are raised. The
#: same problem in an *included* file arrives wrapped in the trace for the
#: include, and is a local failure: a library whose root is a sequence should not
#: abandon a parse of the document that used it.
#:
#: A `raml.entry_point is None` test would be tidier and is wrong. A root that is
#: not a mapping fails *after* the fragment is registered, so it would look
#: recoverable; a bad type declaration fails *before* `entry_point` is assigned,
#: so it would look fatal. The two need telling apart and only the message does
#: it.
_FATAL: Final = frozenset(
    {
        'load resource',  # the entry file could not be read
        'unknown fragment kind',  # no RAML header, or one nothing recognises
        'fragment kind not supported',  # Overlay and Extension, until v1.1
        'unexpected fragment kind',  # the header contradicts the context
        'must be map',  # the root is not a mapping
    }
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
    #
    # Assigned in two steps rather than one so that a lenient caller gets the
    # partial fragment: `decode_fragment` registers it before decoding its body,
    # so a failure inside the body still leaves something worth reading. A root
    # that is not a mapping fails *before* the registration, which is what makes
    # `entry_point is None` the test for "nothing to hand back".
    raml.entry_point = decode_fragment(raml, uri, kind, text)

    # P4 — build endpoints from the API's resources, in two stages, and P6 —
    # propagate URI parameters down the tree. API only; a Library has none.
    build_endpoints(raml)

    # P5 — resolve `securedBy:` inheritance and bind every reference to the
    # scheme it names. After P4 because it walks `raml.endpoints`.
    apply_security_schemes(raml)

    # P7 — drain the unknown worklist: every declaration whose kind the document
    # alone could not settle now gets one. After this, invariant I5 holds.
    resolve_shapes(raml)

    # And the one declaration rule that cannot wait for P10: a discriminator is
    # inherited, so after P9 every subtype of a discriminated type looks like an
    # inline declaration that wrote one (docs/05 § 9).
    check_declared_discriminators(raml)

    # P8 — bind every `(annotation)` application to the type it names.
    # Unconditional: an undeclared annotation is malformed input whether or not
    # the caller asked to unwrap or validate.
    resolve_domain_extensions(raml)

    # P9 — flatten every inheritance chain, then mark the cycles. Opt-in: the
    # un-flattened model is what a formatter or a doc generator wants.
    if options.unwrap:
        unwrap_shapes(raml)

    # P10 — check every declaration and validate every example, default,
    # custom facet and annotation value. Opt-in; when P9 did not run, each
    # declaration is validated against a private unwrapped copy of itself.
    if options.validate:
        validate_shapes(raml)
    return raml
