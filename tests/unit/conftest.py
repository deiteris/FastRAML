"""Shared helpers for the unit tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fastraml.loaders import SafeFileLoader

if TYPE_CHECKING:
    from pathlib import Path


def write_files(root: Path, files: dict[str, str]) -> Path:
    """Write a whole workspace at once, creating intermediate directories.

    Keys are POSIX-style relative paths so a test reads as a directory listing.
    """
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline='' keeps the bytes exactly as written, so a test that
        # asserts on included text is not at the mercy of the platform.
        with target.open('w', encoding='utf-8', newline='') as handle:
            handle.write(content)
    return root


class CountingLoader:
    """A `SafeFileLoader` that tallies reads, per URI and in total.

    Used to prove the two caches: a file referenced from many places is read
    once (docs/02 invariants I2 and I3).
    """

    def __init__(self, root: Path) -> None:
        self._inner = SafeFileLoader(root)
        self.calls: list[tuple[str, int | None]] = []

    @property
    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for uri, _limit in self.calls:
            tally[uri] = tally.get(uri, 0) + 1
        return tally

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        self.calls.append((uri, max_bytes))
        return self._inner.load(uri, max_bytes=max_bytes)


@pytest.fixture
def workspace(tmp_path: Path):
    """Write a set of files under `tmp_path` and return the directory."""

    def build(files: dict[str, str]) -> Path:
        return write_files(tmp_path, files)

    return build
