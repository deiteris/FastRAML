"""The committed golden records, and the gates run over what was generated.

Two different questions, and the second is the one worth having.

The golden says *what* was generated, so a change to a template or a spelling
shows up as a diff a reader can look at. That is the repository's idiom and
openapi-python-client's.

The gates say the output is real code: `ruff check`, `mypy --strict` and
`compileall` over the generated package, using the `pyproject.toml` it generated
for itself. A golden can be green while every file in it fails to parse.

Both targets go through both.

`ruff format --check` is deliberately *not* among them. The generator does not
run a formatter -- doing so made its output a function of the installed ruff
version -- so it lays out what it can and does not claim to produce what some
other version of a formatter would.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import GOLDEN, TARGETS

_IGNORED = frozenset({'__pycache__', '.ruff_cache', '.mypy_cache'})


@pytest.fixture(params=sorted(TARGETS), scope='session')
def target(request):
    return request.param


@pytest.fixture(scope='session')
def output(target, generated, served):
    return {'python-httpx': generated, 'python-fastapi': served}[target]


class TestTheGoldenRecord:
    def test_regenerating_changes_nothing(self, target, output):
        root = GOLDEN / target
        current = {
            str(path.relative_to(root)).replace('\\', '/'): path.read_text(encoding='utf-8')
            for path in sorted(root.rglob('*'))
            # A dotted directory is a tool's cache and `__pycache__` is what
            # importing the golden leaves behind; neither is generated output.
            if path.is_file() and not (_IGNORED & set(path.relative_to(root).parts))
        }
        expected = {name: text for name, text in output.files.items() if text}
        assert sorted(current) == sorted(expected), 'run `uv run python tests/regenerate_golden.py`'
        for name in sorted(expected):
            # Compared file by file: one assertion over the whole mapping puts a
            # forty-file diff on screen for a one-line change.
            assert current[name] == expected[name], f'{name} is stale -- regenerate the golden'


class TestTheOutputIsRealCode:
    @pytest.fixture(scope='class')
    @staticmethod
    def written(output, tmp_path_factory):
        destination = tmp_path_factory.mktemp('generated')
        output.write(destination)
        return destination

    def test_it_passes_ruff(self, written):
        result = _run(['-m', 'ruff', 'check', '--no-cache', str(written)], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_it_passes_mypy_strict(self, written, output):
        # `strict = true` is what the generated `pyproject.toml` declares, so
        # this is the package's own gate rather than one chosen here.
        package = written / output.package.replace('-', '_')
        result = _run(['-m', 'mypy', str(package)], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_every_module_compiles(self, written):
        result = _run(['-m', 'compileall', '-q', str(written)], cwd=written)
        assert result.returncode == 0, result.stdout + result.stderr


class TestTheStubIsGeneratedButNotOwned:
    """`impl.py` is written once and belongs to whoever reads it from then on.

    It is still generated, so it is still gated: a stub that does not type-check
    is a stub nobody can start from.
    """

    def test_it_sits_beside_the_package_and_not_inside_it(self, served):
        assert 'impl.py' in served.files
        assert not any(name.startswith('bookstore_server/impl') for name in served.files)

    def test_it_carries_no_generated_header(self, served):
        # Nothing rewrites it, so a "do not edit" line would be a lie.
        assert 'Do not edit' not in served.files['impl.py']

    def test_it_stubs_every_operation(self, served):
        # One stub per abstract method, so a subclass of `Api` that copied this
        # file can be constructed. `abc` is what would otherwise refuse it.
        abstract = sum(
            text.count('@abc.abstractmethod')
            for name, text in served.files.items()
            if name.startswith('bookstore_server/api/')
        )
        assert abstract > 0
        assert served.files['impl.py'].count('raise NotImplementedError') == abstract

    def test_it_passes_mypy_strict(self, served, tmp_path):
        served.write(tmp_path)
        result = _run(['-m', 'mypy', 'impl.py'], cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr


def _run(arguments, cwd):
    return subprocess.run(
        [sys.executable, *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
