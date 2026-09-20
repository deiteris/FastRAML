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

__all__ = ['TARGETS', 'Generated', 'Settings', 'generate']


@dataclass(frozen=True, slots=True)
class Settings:
    """What the caller decides, as opposed to what the document says."""

    #: The distribution name. Defaults to the API title, slugified.
    package: str | None = None
    #: Extra target-specific options, so a target can grow one without moving
    #: this dataclass.
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Generated:
    """What a target produced: relative paths to file contents."""

    package: str
    files: Mapping[str, str]

    def write(self, destination: pathlib.Path) -> list[pathlib.Path]:
        """Write every file under `destination`, creating directories."""
        written = []
        for relative, text in self.files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            written.append(path)
        return written


class Target(Protocol):
    def __call__(self, tree: Tree, settings: Settings) -> Generated: ...


def _python_target(tree: Tree, settings: Settings) -> Generated:
    # Imported inside the call so the registry costs nothing to list, and a
    # target with a heavy dependency does not load for a caller using another.
    from .python import generate_python  # noqa: PLC0415 - deferred so listing the registry loads nothing

    return generate_python(tree, settings)


def _fastapi_target(tree: Tree, settings: Settings) -> Generated:
    from .fastapi import generate_fastapi  # noqa: PLC0415 - as above

    return generate_fastapi(tree, settings)


#: The two directions a document can be read in. `python` calls an API that
#: exists; `fastapi` states the one to write.
TARGETS: dict[str, Target] = {'fastapi': _fastapi_target, 'python': _python_target}


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
