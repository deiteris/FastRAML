"""`fastmcp-raml`, the command line.

The sample's `baseUri` is `https://{tenant}.books.example.com/{version}`, which
is the case the flags exist for: nothing can be sent until `tenant` is bound,
the API is somewhere else, or a mock answers instead.
"""

from __future__ import annotations

import pytest
from conftest import SAMPLE, SAMPLE_ROOT
from fastmcp import Client
from typer.testing import CliRunner

from fastmcp_raml import RAMLProvider
from fastmcp_raml.cli import app, build

WORKSPACE = ['--workspace', str(SAMPLE_ROOT)]


def run(*arguments: str):
    return CliRunner().invoke(app, [str(SAMPLE), *WORKSPACE, *arguments])


def client_of(server):
    provider = next(p for p in server.providers if isinstance(p, RAMLProvider))
    return provider._client


class TestDescribe:
    def test_it_lists_every_operation_and_the_documentation(self):
        result = run('--describe')
        assert result.exit_code == 0, result.output
        assert 'get_books_isbn' in result.output
        assert 'docs://pagination' in result.output

    def test_it_needs_no_base_url(self):
        # Describing sends nothing, so an unbound `{tenant}` is not an error.
        assert run('--describe').exit_code == 0


class TestWhereRequestsGo:
    def test_an_unbound_base_uri_parameter_names_the_flag_that_binds_it(self):
        result = run()
        assert result.exit_code == 2
        assert '--param tenant=' in result.output

    def test_param_binds_the_base_uri(self):
        server = build(SAMPLE, workspace=SAMPLE_ROOT, parameters={'tenant': 'acme'})
        assert str(client_of(server).base_url) == 'https://acme.books.example.com/v2/'

    def test_base_url_replaces_the_documents(self):
        server = build(SAMPLE, workspace=SAMPLE_ROOT, base_url='https://staging.example/v1')
        assert str(client_of(server).base_url) == 'https://staging.example/v1/'

    def test_headers_ride_every_request(self):
        server = build(
            SAMPLE, workspace=SAMPLE_ROOT, base_url='https://x.example', headers={'Authorization': 'Bearer t'}
        )
        assert client_of(server).headers['Authorization'] == 'Bearer t'

    @pytest.mark.parametrize(
        ('flag', 'value'), [('--header', 'no-colon'), ('--param', 'no-equals'), ('--header', ': v')]
    )
    def test_a_malformed_pair_is_refused(self, flag, value):
        result = run('--describe', flag, value)
        assert result.exit_code == 2
        assert flag in result.output

    def test_mock_and_base_url_contradict_each_other(self):
        result = run('--mock', '--base-url', 'https://x.example')
        assert result.exit_code == 2
        assert '--base-url' in result.output


class TestMock:
    async def test_a_tool_call_is_answered_by_the_mock(self):
        server = build(SAMPLE, workspace=SAMPLE_ROOT, mock=True)
        async with Client(server) as connected:
            result = await connected.call_tool('get_books_isbn', {'isbn': '9780441013593'})
        assert not result.is_error
        assert 'title' in result.structured_content

    def test_an_unreadable_mock_config_is_refused(self, tmp_path):
        result = run('--mock-config', str(tmp_path / 'absent.json'))
        assert result.exit_code == 2
        assert '--mock-config' in result.output


def test_a_document_that_does_not_parse_exits_2(tmp_path):
    broken = tmp_path / 'api.raml'
    broken.write_text('#%RAML 1.0\ntitle: Broken\ntypes:\n  A:\n    type: Nowhere\n', encoding='utf-8')
    result = CliRunner().invoke(app, [str(broken), '--describe'])
    assert result.exit_code == 2
    assert 'api.raml' in result.output
