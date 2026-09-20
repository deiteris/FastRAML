"""The CLI, including the path that needs no RAML at all."""

from __future__ import annotations

import json

from conftest import FIXTURE, WORKSPACE, envelope
from typer.testing import CliRunner

from raml_codegen.cli import app

runner = CliRunner()


def test_it_lists_its_targets():
    result = runner.invoke(app, ['targets'])
    assert result.exit_code == 0
    assert 'python' in result.stdout


def test_it_generates_from_a_raml_file(tmp_path):
    result = runner.invoke(
        app,
        ['python', str(FIXTURE), '-o', str(tmp_path), '-w', str(WORKSPACE)],
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'bookstore_api' / 'models' / 'book.py').exists()


def test_it_generates_from_a_projected_tree(tmp_path, document):
    # The point of a published wire format: a machine with the JSON and no RAML
    # files can still generate, which is what `fastraml tree > api.json` is for.
    source = tmp_path / 'api.json'
    source.write_text(json.dumps(document), encoding='utf-8')
    result = runner.invoke(app, ['python', str(source), '-o', str(tmp_path / 'out')])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'out' / 'bookstore_api' / 'models' / 'book.py').exists()


def test_the_package_name_can_be_chosen(tmp_path, document):
    source = tmp_path / 'api.json'
    source.write_text(json.dumps(document), encoding='utf-8')
    result = runner.invoke(app, ['python', str(source), '-o', str(tmp_path / 'out'), '--package', 'books-client'])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / 'out' / 'books_client' / '__init__.py').exists()


def test_an_unreadable_envelope_is_an_error_and_not_a_traceback(tmp_path):
    source = tmp_path / 'api.json'
    source.write_text(json.dumps(envelope(format_version=99)), encoding='utf-8')
    result = runner.invoke(app, ['python', str(source), '-o', str(tmp_path / 'out')])
    assert result.exit_code == 2
    assert 'regenerate' in result.output
