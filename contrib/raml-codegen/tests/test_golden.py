"""The committed golden record, and the gates run over what was generated.

Two different questions, and the second is the one worth having.

The golden says *what* was generated, so a change to a template or a spelling
shows up as a diff a reader can look at. That is the repository's idiom and
openapi-python-client's.

The gates say the output is real code: `ruff check`, `mypy --strict` and
`compileall` over the generated package, using the `pyproject.toml` it generated
for itself. A golden can be green while every file in it fails to parse.

`ruff format --check` is deliberately *not* among them. The generator does not
run a formatter -- doing so made its output a function of the installed ruff
version -- so it lays out what it can and does not claim to produce what some
other version of a formatter would.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import GOLDEN

_IGNORED = frozenset({'__pycache__', '.ruff_cache', '.mypy_cache'})


class TestTheGoldenRecord:
    def test_regenerating_changes_nothing(self, generated):
        current = {
            str(path.relative_to(GOLDEN)).replace('\\', '/'): path.read_text(encoding='utf-8')
            for path in sorted(GOLDEN.rglob('*'))
            # A dotted directory is a tool's cache and `__pycache__` is what
            # importing the golden leaves behind; neither is generated output.
            if path.is_file() and not (_IGNORED & set(path.relative_to(GOLDEN).parts))
        }
        expected = {name: text for name, text in generated.files.items() if text}
        assert sorted(current) == sorted(expected), 'run `uv run python -m tests.regenerate_golden`'
        for name in sorted(expected):
            # Compared file by file: one assertion over the whole mapping puts a
            # forty-file diff on screen for a one-line change.
            assert current[name] == expected[name], f'{name} is stale -- regenerate the golden'


class TestTheOutputIsRealCode:
    @pytest.fixture(scope='class')
    @staticmethod
    def written(generated, tmp_path_factory):
        destination = tmp_path_factory.mktemp('generated')
        generated.write(destination)
        return destination

    def test_it_passes_ruff(self, written, generated):
        result = _run(['-m', 'ruff', 'check', '--no-cache', str(written)], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_it_passes_mypy_strict(self, written, generated):
        # `strict = true` is what the generated `pyproject.toml` declares, so
        # this is the package's own gate rather than one chosen here.
        package = written / generated.package.replace('-', '_')
        result = _run(['-m', 'mypy', str(package)], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_every_module_compiles(self, written):
        result = _run(['-m', 'compileall', '-q', str(written)], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr


def _run(arguments, cwd):
    return subprocess.run(
        [sys.executable, *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
