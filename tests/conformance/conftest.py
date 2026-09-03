"""Fixtures for the conformance suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.tck.conftest import tck_root

if TYPE_CHECKING:
    from pathlib import Path

#: Every YAML-ish file in the corpus, not only the entry documents: an included
#: library or data type is exactly where an unusual scalar hides.
_SUFFIXES = ('.raml', '.yaml', '.yml')


@pytest.fixture(scope='session')
def tck_documents() -> list[Path]:
    root = tck_root()
    if root is None:
        return []
    return sorted(path for path in root.rglob('*') if path.suffix.lower() in _SUFFIXES and path.is_file())
