"""Shared helpers for the unit tests."""

from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from fastraml.loaders import SafeFileLoader
from fastraml.parser.entry import ParseOptions, parse_from_path, parse_lenient
from fastraml.uris import file_uri_to_path, path_to_file_uri

if TYPE_CHECKING:
    from pathlib import Path

    from fastraml.errors import RamlError
    from fastraml.loaders import ResourceLoader
    from fastraml.registry import Raml


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


class MemoryWorkspace:
    """Path-shaped test inputs served through the parser's loader interface."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, bytes] = {}

    def __call__(self, files: dict[str, str]) -> Path:
        self.files.update({path_to_file_uri(self.root / name): text.encode('utf-8') for name, text in files.items()})
        return self.root

    def load(self, uri: str, *, max_bytes: int | None = None) -> bytes:
        # Disk loading drops a file URI's query and fragment before opening it.
        path = file_uri_to_path(uri)
        try:
            data = self.files[path_to_file_uri(path)]
        except KeyError:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path) from None
        return data if max_bytes is None else data[: max_bytes + 1]

    def _options(self, options: ParseOptions | None) -> ParseOptions:
        given = options or ParseOptions()
        return replace(given, file_loader=self) if given.file_loader is None else given

    def parse(self, path: Path, options: ParseOptions | None = None) -> Raml:
        return parse_from_path(path, self._options(options))

    def lenient(self, path: Path, options: ParseOptions | None = None) -> tuple[Raml, RamlError | None]:
        return parse_lenient(path, self._options(options))


@pytest.fixture
def workspace(request: pytest.FixtureRequest) -> MemoryWorkspace:
    """Give each test an absolute URI base without creating a directory."""
    key = hashlib.blake2b(request.node.nodeid.encode(), digest_size=12).hexdigest()
    return MemoryWorkspace(request.config.rootpath / '.fastraml-virtual' / key)


@pytest.fixture
def memory_workspace(workspace: MemoryWorkspace) -> MemoryWorkspace:
    """`workspace`, for a module where that name is a service `Workspace`."""
    return workspace


class CountingLoader:
    """A loader that tallies reads, per URI and in total.

    Used to prove the two caches: a file referenced from many places is read
    once (docs/02 invariants I2 and I3).
    """

    def __init__(self, root: Path, inner: ResourceLoader | None = None) -> None:
        self._inner = inner if inner is not None else SafeFileLoader(root)
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
def disk_workspace(tmp_path: Path):
    """Write a set of files under `tmp_path` and return the directory: for a
    test of filesystem behaviour, or a caller that reads paths itself.
    """

    def build(files: dict[str, str]) -> Path:
        return write_files(tmp_path, files)

    return build
