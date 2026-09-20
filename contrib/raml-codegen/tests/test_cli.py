"""The CLI, including the path that needs no RAML at all."""

from __future__ import annotations

import json

from conftest import TREE, envelope
from typer.testing import CliRunner

from raml_codegen.cli import app

runner = CliRunner()


def test_it_lists_its_targets():
    result = runner.invoke(app, ['targets'])
    assert result.exit_code == 0
    assert 'python-httpx' in result.stdout
    assert 'python-fastapi' in result.stdout


def test_it_generates_a_server_from_the_same_document(tmp_path):
    # The same tree, read the other way round: `python-httpx` calls the API and
    # `python-fastapi` states the one to write.
    result = runner.invoke(app, ['python-fastapi', str(TREE), '-o', str(tmp_path), '--package', 'bookstore-server'])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'bookstore_server' / 'api' / 'books.py').exists()
    assert (tmp_path / 'impl.py').exists()


def test_it_says_when_it_kept_a_file_rather_than_writing_one(tmp_path):
    # A developer who is not told assumes the new stub arrived, and then
    # wonders why the method they were told about is not in the file.
    arguments = ['python-fastapi', str(TREE), '-o', str(tmp_path), '--package', 'bookstore-server']
    runner.invoke(app, arguments)
    (tmp_path / 'impl.py').write_text('# mine\n', encoding='utf-8')

    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.stdout
    assert 'kept README.md, impl.py, pyproject.toml' in result.output
    assert (tmp_path / 'impl.py').read_text(encoding='utf-8') == '# mine\n'

    forced = runner.invoke(app, [*arguments, '--force'])
    assert forced.exit_code == 0, forced.stdout
    assert (tmp_path / 'impl.py').read_text(encoding='utf-8') != '# mine\n'


def test_generating_into_a_project_leaves_the_project_alone(tmp_path):
    # The output directory is meant to *be* the service, so it has to survive
    # being one: a second run may not eat the dependencies the implementation
    # needs, and it did until this was pinned.
    arguments = ['python-fastapi', str(TREE), '-o', str(tmp_path), '--package', 'bookstore-server']
    runner.invoke(app, arguments)
    mine = '[project]\nname = "my-service"\ndependencies = ["fastapi", "asyncpg"]\n'
    (tmp_path / 'pyproject.toml').write_text(mine, encoding='utf-8')
    (tmp_path / 'README.md').write_text('# My service\n', encoding='utf-8')

    assert runner.invoke(app, arguments).exit_code == 0
    assert (tmp_path / 'pyproject.toml').read_text(encoding='utf-8') == mine
    assert (tmp_path / 'README.md').read_text(encoding='utf-8') == '# My service\n'


def test_it_generates_from_a_tree_document(tmp_path):
    result = runner.invoke(app, ['python-httpx', str(TREE), '-o', str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'bookstore_api' / 'models' / 'book.py').exists()


def test_it_reads_a_tree_from_stdin(tmp_path, document):
    # So `fastraml tree api.raml | raml-codegen python - -o out/` is a pipeline
    # and nothing has to touch the disk between the two.
    result = runner.invoke(app, ['python-httpx', '-', '-o', str(tmp_path)], input=json.dumps(document))
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'bookstore_api' / 'models' / 'book.py').exists()


def test_raml_is_not_an_input(tmp_path):
    # This package has no parser. Saying so beats a traceback about a byte that
    # is not JSON.
    source = tmp_path / 'api.raml'
    source.write_text('#%RAML 1.0\ntitle: Nope\n', encoding='utf-8')
    result = runner.invoke(app, ['python-httpx', str(source), '-o', str(tmp_path / 'out')])
    assert result.exit_code == 2
    assert 'not JSON' in result.output


def test_the_package_name_can_be_chosen(tmp_path, document):
    source = tmp_path / 'api.json'
    source.write_text(json.dumps(document), encoding='utf-8')
    result = runner.invoke(app, ['python-httpx', str(source), '-o', str(tmp_path / 'out'), '--package', 'books-client'])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'out' / 'books_client' / '__init__.py').exists()


def test_an_unreadable_envelope_is_an_error_and_not_a_traceback(tmp_path):
    source = tmp_path / 'api.json'
    source.write_text(json.dumps(envelope(format_version=99)), encoding='utf-8')
    result = runner.invoke(app, ['python-httpx', str(source), '-o', str(tmp_path / 'out')])
    assert result.exit_code == 2
    assert 'regenerate' in result.output
