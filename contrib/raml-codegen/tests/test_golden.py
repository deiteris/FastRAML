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

import copy
import subprocess
import sys

import pytest
from conftest import GOLDEN, TARGETS

from raml_codegen import generate

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

    def test_regenerating_overwrites_none_of_the_scaffolding(self, served, tmp_path):
        # The whole point of the output directory being usable as the service.
        # `impl.py` is the implementation, `pyproject.toml` grows the
        # dependencies that implementation needs, and the README stops
        # describing a generator. None of it can be produced again.
        served.write(tmp_path)
        mine = {}
        for name in ('impl.py', 'pyproject.toml', 'README.md'):
            mine[name] = (tmp_path / name).read_text(encoding='utf-8') + '\n# MINE\n'
            (tmp_path / name).write_text(mine[name], encoding='utf-8')

        again = served.write(tmp_path)
        for name, text in mine.items():
            assert (tmp_path / name).read_text(encoding='utf-8') == text, name
            assert (tmp_path / name) not in again.paths
        assert sorted(path.name for path in again.kept) == ['README.md', 'impl.py', 'pyproject.toml']

    def test_the_package_around_it_is_rewritten(self, served, tmp_path):
        # Keeping one file is not the same as keeping the directory. Everything
        # the document owns is regenerated whole, which is what makes it safe
        # to read.
        served.write(tmp_path)
        routes = tmp_path / 'bookstore_server' / 'api' / 'books.py'
        routes.write_text('# clobbered\n', encoding='utf-8')
        served.write(tmp_path)
        assert routes.read_text(encoding='utf-8') != '# clobbered\n'

    def test_force_overwrites_it(self, served, tmp_path):
        served.write(tmp_path)
        (tmp_path / 'impl.py').write_text('# mine\n', encoding='utf-8')
        again = served.write(tmp_path, force=True)
        assert (tmp_path / 'impl.py').read_text(encoding='utf-8') == served.files['impl.py']
        assert not again.kept

    def test_the_client_target_keeps_nothing(self, generated, tmp_path):
        # Every file a client target writes is an artefact. There is no
        # starting point among them, so nothing is write-once.
        generated.write(tmp_path)
        assert not generated.write(tmp_path).kept


class TestTheStubSaysWhenTheDocumentMovesUnderIt:
    """Three ways a document can change, and what reports each.

    The stub is generated once and then edited, so it is the file most likely
    to fall behind the document. None of this finds out *how* to implement an
    operation -- only that one is waiting, or gone, or no longer the shape it
    was, which is the part a person should not have to go looking for.
    """

    def test_a_gained_operation_is_named_at_startup(self, document, tmp_path):
        # `abc` refuses the class, so this is a failure to start rather than a
        # 500 on the first request that reaches an unrouted path.
        _write(_with_put_books(document), tmp_path, keep_stub_from=document)
        failed = _run(_IMPORT_STUB, cwd=tmp_path)
        assert failed.returncode != 0
        assert "abstract method 'put_books'" in failed.stderr

    def test_a_gained_operation_is_also_a_type_error(self, document, tmp_path):
        _write(_with_put_books(document), tmp_path, keep_stub_from=document)
        assert 'put_books' in _mypy(tmp_path)

    def test_a_changed_signature_is_an_incompatible_override(self, document, tmp_path):
        narrowed = copy.deepcopy(document)
        narrowed['endpoints']['/search']['operations']['get']['query_parameters'] = {}
        _write(narrowed, tmp_path, keep_stub_from=document)
        reported = _mypy(tmp_path)
        assert 'Signature of "get_search" incompatible with supertype' in reported

    def test_a_dropped_operation_is_an_override_of_nothing(self, document, tmp_path):
        # This is what `@override` on every generated method buys, and it is
        # the only one of the three that nothing else catches: `abc` does not
        # mind a subclass having extra methods, and a method with no base
        # declaration is ordinary Python.
        shrunk = copy.deepcopy(document)
        del shrunk['endpoints']['/books/{isbn}']['operations']['delete']
        _write(shrunk, tmp_path, keep_stub_from=document)
        reported = _mypy(tmp_path)
        assert 'Method "delete_books_isbn" is marked as an override' in reported

    def test_a_dropped_operation_would_otherwise_pass(self, document, tmp_path):
        # The same case with the decorators taken off, to say what the decorator
        # is for rather than assert it into the dark.
        shrunk = copy.deepcopy(document)
        del shrunk['endpoints']['/books/{isbn}']['operations']['delete']
        _write(shrunk, tmp_path, keep_stub_from=document)
        stub = tmp_path / 'impl.py'
        stub.write_text(stub.read_text(encoding='utf-8').replace('    @override\n', ''), encoding='utf-8')
        assert 'no issues found' in _mypy(tmp_path)

    def test_every_generated_method_carries_it(self, served):
        # Indented, so the one in the docstring that explains it is not counted.
        stub = served.files['impl.py']
        decorated = sum(1 for line in stub.splitlines() if line == '    @override')
        assert decorated == stub.count('    async def ')
        assert decorated > 0


_IMPORT_STUB = "import sys; sys.path.insert(0, '.'); import impl"


def _with_put_books(document):
    grown = copy.deepcopy(document)
    operations = grown['endpoints']['/books']['operations']
    operations['put'] = copy.deepcopy(operations['post'])
    operations['put']['display_name'] = 'Replace a book'
    return grown


def _write(document, destination, *, keep_stub_from):
    """Generate `document`, but keep the stub the *original* document produced.

    That is the developer's situation exactly: a file written once and edited
    since, beside a package that has been regenerated from a document that has
    moved on.
    """
    settings = TARGETS['python-fastapi']
    generate(keep_stub_from, 'python-fastapi', settings).write(destination)
    return generate(document, 'python-fastapi', settings).write(destination)


def _mypy(cwd):
    # Plain text whatever the environment: mypy colours under `FORCE_COLOR`,
    # and the escapes land inside the messages these tests read.
    return _run(['-m', 'mypy', '--no-color-output', 'impl.py'], cwd=cwd).stdout


def _run(arguments, cwd):
    """Run the interpreter, with a string meaning `-c`."""
    if isinstance(arguments, str):
        arguments = ['-c', arguments]
    return subprocess.run(
        [sys.executable, *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
