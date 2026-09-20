"""The target registry, and the one way in.

A target takes a `Tree` and `Settings` and returns files: relative paths mapped
to text. Nothing is written to disk until the caller asks. That is what lets the
suite generate into `tmp_path` and lets the golden test compare in memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ..reader import Tree

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Mapping

__all__ = ['TARGETS', 'Generated', 'Settings', 'Written', 'generate']


@dataclass(frozen=True, slots=True)
class Settings:
    """What the caller decides, as opposed to what the document says."""

    #: The distribution name. Defaults to the API title, slugified.
    package: str | None = None
    #: Extra target-specific options, so a target can grow one without moving
    #: this dataclass.
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Written:
    """What one `write` did, so a caller can say which files it left alone."""

    paths: tuple[pathlib.Path, ...]
    #: Files in `Generated.once` that were already there, and so were not
    #: touched. These are the ones a developer has been editing.
    kept: tuple[pathlib.Path, ...]

    def __len__(self) -> int:
        return len(self.paths)


@dataclass(frozen=True, slots=True)
class Generated:
    """What a target produced: relative paths to file contents."""

    package: str
    files: Mapping[str, str]
    #: Paths to write only where nothing is there already.
    #:
    #: A generated package is regenerated whole, every time -- that is what
    #: makes it safe to read. A *starting point* is the opposite thing: it is
    #: generated once and then belongs to whoever edits it, and the `fastapi`
    #: target's `impl.py` is one. Overwriting it on the next run would destroy
    #: the work the generator exists to make possible.
    once: frozenset[str] = frozenset()

    def write(self, destination: pathlib.Path, *, force: bool = False) -> Written:
        """Write every file under `destination`, creating directories.

        A path in `once` that already exists is left alone unless `force`.
        """
        written: list[pathlib.Path] = []
        kept: list[pathlib.Path] = []
        for relative, text in self.files.items():
            path = destination / relative
            if relative in self.once and path.exists() and not force:
                kept.append(path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            written.append(path)
        return Written(paths=tuple(written), kept=tuple(kept))


class Target(Protocol):
    def __call__(self, tree: Tree, settings: Settings) -> Generated: ...


def _python_fastapi(tree: Tree, settings: Settings) -> Generated:
    # Imported inside the call so the registry costs nothing to list, and a
    # target with a heavy dependency does not load for a caller using another.
    from .python.fastapi import generate_fastapi  # noqa: PLC0415 - deferred so listing the registry loads nothing

    return generate_fastapi(tree, settings)


def _python_httpx(tree: Tree, settings: Settings) -> Generated:
    from .python.httpx import generate_httpx  # noqa: PLC0415 - as above

    return generate_httpx(tree, settings)


#: A target is named `<language>-<library>`, and its module path is the name
#: with the dash as a dot: `python-httpx` is `targets.python.httpx`. The two
#: here read one document in opposite directions -- `httpx` writes the caller,
#: `fastapi` writes the thing being called.
TARGETS: dict[str, Target] = {'python-fastapi': _python_fastapi, 'python-httpx': _python_httpx}


def generate(document: object, target: str, settings: Settings | None = None) -> Generated:
    """Run one target over a `fastraml tree` document.

    `document` is the decoded JSON. It is not RAML and not a path to RAML:
    producing it needs the parser, and this package does not have one. Run
    `fastraml tree api.raml > api.json` wherever the RAML lives, then bring the
    JSON here.
    """
    chosen = TARGETS.get(target)
    if chosen is None:
        raise LookupError(f'no such target: {target!r} (have {", ".join(sorted(TARGETS))})')
    return chosen(Tree.of(document), settings or Settings())
