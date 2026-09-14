from __future__ import annotations

import json
import math

import pytest
from typer.testing import CliRunner

from raml_mock.cli import app
from raml_mock.config_io import load_options


def test_json_configuration_loads_every_declarative_policy(tmp_path) -> None:
    path = tmp_path / 'mock.json'
    path.write_text(
        json.dumps(
            {
                'generation': {'seed': 'demo', 'collectionSize': 3, 'optionalProbability': 0.5},
                'routes': {'GET /events': {'delay': 0.1}},
                'authentication': {
                    'basic': {'basic': [{'username': 'demo', 'password': 'secret'}]},
                    'bearer': {'oauth': [{'token': 'token', 'scopes': ['read']}]},
                },
                'resources': [
                    {
                        'name': 'items',
                        'keyField': 'id',
                        'keyParameter': 'id',
                        'seedFromExample': True,
                        'collectionGet': 'GET /items',
                        'itemGet': 'GET /items/{id}',
                    }
                ],
            }
        ),
        encoding='utf-8',
    )
    options = load_options(path)
    assert options.generation.collection_size == 3
    assert options.routes[('GET', '/events')].delay == 0.1
    assert options.authentication is not None
    assert options.authentication.basic['basic'][0].username == 'demo'
    assert options.authentication.bearer['oauth'][0].scopes == {'read'}
    assert options.resources[0].item_get == ('GET', '/items/{id}')
    assert options.resources[0].seed_from_example is True


def test_cli_help_and_server_construction(source, monkeypatch) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ['--help']).exit_code == 0
    seen = {}

    def run_app(application, **kwargs):
        seen['app'] = application
        seen.update(kwargs)

    monkeypatch.setattr('raml_mock.cli.web.run_app', run_app)
    result = runner.invoke(app, [str(source), '--host', '127.0.0.2', '--port', '9000', '--seed', 'suite'])
    assert result.exit_code == 0
    assert seen['host'] == '127.0.0.2'
    assert seen['port'] == 9000


def test_json_configuration_rejects_non_finite_numbers(tmp_path) -> None:
    path = tmp_path / 'mock.json'
    path.write_text(json.dumps({'generation': {'optionalProbability': math.nan}}), encoding='utf-8')
    with pytest.raises(ValueError, match='not valid JSON'):
        load_options(path)


@pytest.mark.parametrize(
    ('value', 'field'),
    [
        ({'authentiction': {}}, 'authentiction'),
        ({'generation': {'collectionSzie': 2}}, 'collectionSzie'),
        ({'routes': {'GET /events': {'delayy': 2}}}, 'delayy'),
        ({'authentication': {'basic': {'basic': [{'username': 'a', 'password': 'b', 'extra': True}]}}}, 'extra'),
        ({'resources': [{'name': 'x', 'keyField': 'id', 'keyParameter': 'id', 'creat': 'POST /x'}]}, 'creat'),
    ],
)
def test_json_configuration_rejects_unknown_fields(tmp_path, value, field) -> None:
    path = tmp_path / 'mock.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match=field):
        load_options(path)


def test_cli_reports_configuration_errors_without_a_traceback(source, tmp_path) -> None:
    path = tmp_path / 'mock.json'
    path.write_text(json.dumps({'authentiction': {}}), encoding='utf-8')
    result = CliRunner().invoke(app, [str(source), '--config', str(path)])
    assert result.exit_code == 2
    assert 'Invalid value for --config' in result.output
    assert 'authentiction' in result.output
    assert 'Traceback' not in result.output
