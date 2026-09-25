"""Loading the APIs `conf.py` names, once per build.

`raml_apis` maps a namespace to a root RAML file:

    raml_apis = {'books': 'specs/books.raml'}
    raml_apis = {'books': {'path': 'specs/books.raml', 'workspace_root': 'specs'}}

Every API is parsed when the builder starts, before Sphinx forks for a parallel
read, so each is parsed once and its diagnostics are reported once. A parse is
kept for the life of the process and redone only when a file it read changed,
which is what an auto-rebuilding server needs.

Parsing is fastraml's; this module holds no rule about RAML. It parses with
`unwrap=True`, so the model `catalogue.py` reads is the effective document, and
reports the parser's diagnostics as Sphinx warnings at the RAML file and line
they are about, so `sphinx-build -W` fails on a broken specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from fastraml import ParseOptions, RamlError, parse_lenient
from sphinx.errors import ConfigError
from sphinx.util import logging

from .catalogue import Catalogue

if TYPE_CHECKING:
    from fastraml import Raml
    from sphinx.application import Sphinx
    from sphinx.config import Config
    from sphinx.environment import BuildEnvironment

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Source:
    """Where one API's root file is, as `conf.py` said it."""

    name: str
    path: Path
    workspace_root: Path | None


@dataclass(frozen=True, slots=True)
class Api:
    """One parsed API under its namespace: what it makes addressable, and the files it was read from."""

    name: str
    catalogue: Catalogue
    #: Every local file the parse read, so a page that renders this API is
    #: rebuilt when any of them changes, not only when the root file does.
    files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _Parsed:
    catalogue: Catalogue
    files: tuple[Path, ...]
    mtimes: tuple[float, ...]


#: Parses kept across builds in one process, by root file and workspace root:
#: two namespaces that name one file are one parse.
_CACHE: dict[tuple[Path, Path | None], _Parsed] = {}


def resolve(app: Sphinx, config: Config) -> None:
    """`raml_apis`, checked, with every path made absolute against `conf.py` (`config-inited`).

    Written back as absolute paths, so everything after this reads the
    configuration alone and never needs the application for its directory.
    """
    raw = config.raml_apis
    if not isinstance(raw, dict):
        raise ConfigError(f'raml_apis must map a namespace to a RAML file, got {type(raw).__name__}')
    confdir = Path(app.confdir)
    out: dict[str, dict[str, str | None]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name or ':' in name:
            raise ConfigError(f'raml_apis: {name!r} is not a namespace -- use a non-empty name without ":"')
        if isinstance(value, str | Path):
            path, root = value, None
        elif isinstance(value, dict) and isinstance(value.get('path'), str | Path):
            path, root = value['path'], value.get('workspace_root')
        else:
            raise ConfigError(f'raml_apis[{name!r}] must be a path, or a dict with "path" and "workspace_root"')
        out[name] = {
            'path': str((confdir / path).resolve()),
            'workspace_root': None if root is None else str((confdir / root).resolve()),
        }
    config.raml_apis = out


def sources(config: Config) -> dict[str, Source]:
    """The configured APIs, as `resolve` left them."""
    return {
        name: Source(
            name, Path(value['path']), None if value['workspace_root'] is None else Path(value['workspace_root'])
        )
        for name, value in config.raml_apis.items()
    }


def load_all(app: Sphinx) -> None:
    """Parse every configured API now, in the parent process (`builder-inited`)."""
    for source in sources(app.config).values():
        _load(source)


def api(env: BuildEnvironment, name: str) -> Api | None:
    """One configured API, parsed; `None` when it could not be read at all."""
    source = sources(env.config).get(name)
    return None if source is None else _load(source)


def names(env: BuildEnvironment) -> list[str]:
    return list(env.config.raml_apis)


def _load(source: Source) -> Api | None:
    parsed = _parse(source)
    return None if parsed is None else Api(name=source.name, catalogue=parsed.catalogue, files=parsed.files)


def _parse(source: Source) -> _Parsed | None:
    key = (source.path, source.workspace_root)
    cached = _CACHE.get(key)
    if cached is not None and cached.mtimes == _mtimes(cached.files):
        return cached
    options = ParseOptions(
        unwrap=True,
        validate=True,
        workspace_root=None if source.workspace_root is None else str(source.workspace_root),
    )
    try:
        raml, error = parse_lenient(source.path, options)
    except (RamlError, OSError) as err:
        logger.warning('RAML API %r could not be read: %s', source.name, err, type='fastraml', subtype='parse')
        return None
    if error is not None:
        _report(error)
    files = _files(raml, source.path)
    parsed = _Parsed(Catalogue(raml), files, _mtimes(files))
    _CACHE[key] = parsed
    return parsed


def _report(error: RamlError) -> None:
    """One warning per problem, placed where the author has to act.

    A chain runs from what was being read to why it failed -- `invalid
    example` at the example, caused by `invalid type` at the type it was
    checked against. The warning sits at the outermost frame that has a
    position and names both ends, as `fastraml validate` prints them.
    """
    for chain in error.chains():
        placed = next((frame for frame in chain if frame.position is not None), chain[-1])
        cause = chain[-1]
        message = placed.rendered_message()
        if cause is not placed:
            message = f'{message}: {cause.rendered_message()}'
        where = str(_path(placed.location) or placed.location)
        location = f'{where}:{placed.position.line}' if placed.position is not None else where
        logger.warning(message, location=location, type='fastraml', subtype='parse')


def _files(raml: Raml, entry: Path) -> tuple[Path, ...]:
    """Every local file one parse read: fragments, and every `!include` target."""
    uris = {*raml.fragments, *(ref.abs_uri for refs in raml.include_refs.values() for ref in refs)}
    found = {entry, *(path for uri in uris if (path := _path(uri)) is not None)}
    return tuple(sorted(found))


def _path(uri: str) -> Path | None:
    parts = urlsplit(uri)
    if parts.scheme != 'file':
        return None
    return Path(url2pathname(unquote(parts.path)))


def _mtimes(files: tuple[Path, ...]) -> tuple[float, ...]:
    return tuple(path.stat().st_mtime if path.exists() else -1.0 for path in files)
