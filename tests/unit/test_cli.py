"""The `pyraml` console script — docs/13-public-api.md section 8.

Exit codes and output shape, not parsing. Nothing in `cli.py` decides what is
valid, so the assertions here are about the contract a shell script or a CI job
depends on: what the exit code means, which stream each thing goes to, and that
`--json` stays diffable against the reference implementation's output
(docs/14 section 1.3).
"""

from __future__ import annotations

import json

import pytest

from pyraml.cli import EXIT_INVALID, EXIT_OK, main

API = '#%RAML 1.0\ntitle: Demo\n'
GOOD = API + 'types:\n  T:\n    type: string\n    minLength: 2\n/things:\n  get:\n'
BAD = API + 'types:\n  T:\n    type: integer\n    example: nope\n'


@pytest.fixture
def files(workspace):
    root = workspace({'good.raml': GOOD, 'bad.raml': BAD})
    return lambda name: str(root / name)


class TestExitCodes:
    def test_a_valid_file_exits_zero_and_says_nothing(self, files, capsys):
        assert main(['validate', files('good.raml')]) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == ''

    def test_an_invalid_file_exits_one(self, files, capsys):
        assert main(['validate', files('bad.raml')]) == EXIT_INVALID
        assert 'invalid example' in capsys.readouterr().err

    def test_every_file_is_reported_not_only_the_first(self, files, capsys):
        """go-raml validates the whole list and fails at the end; so does this.

        Stopping at the first failure would make the tool useless for the case
        it exists for — running it over a directory in CI.
        """
        code = main(['validate', files('bad.raml'), files('good.raml'), files('bad.raml')])
        assert code == EXIT_INVALID
        assert capsys.readouterr().err.count('invalid example') == 2

    def test_a_missing_file_exits_one_rather_than_raising(self, files, capsys):
        assert main(['validate', files('absent.raml')]) == EXIT_INVALID
        assert 'load resource' in capsys.readouterr().err


class TestStreams:
    """Diagnostics on stderr, everything else on stdout — so `-v` stays pipeable."""

    def test_diagnostics_go_to_stderr(self, files, capsys):
        main(['validate', files('bad.raml')])
        captured = capsys.readouterr()
        assert captured.out == ''
        assert 'bad.raml: invalid' in captured.err

    def test_verbose_reports_valid_files_on_stdout(self, files, capsys):
        main(['validate', '-v', files('good.raml')])
        captured = capsys.readouterr()
        assert 'good.raml: valid' in captured.out
        assert captured.err == ''

    def test_twice_verbose_adds_the_counts(self, files, capsys):
        main(['validate', '-vv', files('good.raml')])
        out = capsys.readouterr().out
        assert 'backend' in out
        assert 'endpoints' in out


class TestJson:
    def test_one_object_per_file(self, files, capsys):
        main(['validate', '--json', files('good.raml'), files('bad.raml')])
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        records = [json.loads(line) for line in lines]
        assert [record['valid'] for record in records] == [True, False]
        assert records[0]['error'] is None

    def test_the_error_keeps_the_reference_trace_shape(self, files, capsys):
        """`docs/14` § 1.3 diffs this against `raml validate --json`."""
        main(['validate', '--json', files('bad.raml')])
        record = json.loads(capsys.readouterr().out.strip())
        stack = record['error']['traces'][0]['stack']
        assert stack[0]['message'] == 'invalid example'
        assert stack[0]['severity'] == 'error'
        assert ':' in stack[0]['position']

    def test_json_writes_nothing_to_stderr(self, files, capsys):
        """A consumer parses stdout; a stray diagnostic must not corrupt it."""
        main(['validate', '--json', files('bad.raml')])
        assert capsys.readouterr().err == ''


class TestInfo:
    def test_it_reports_the_backend_and_the_counts(self, files, capsys):
        assert main(['info', files('good.raml')]) == EXIT_OK
        out = capsys.readouterr().out
        assert 'backend' in out
        assert 'fragments    1' in out
        assert 'endpoints    1' in out

    def test_an_invalid_file_still_exits_one(self, files, capsys):
        assert main(['info', files('bad.raml')]) == EXIT_INVALID
        assert 'invalid example' in capsys.readouterr().err


class TestOptions:
    def test_the_workspace_root_is_enforced(self, workspace, capsys):
        """`-w` is the sandbox, so an escape has to be refused with it set."""
        root = workspace({'secret.raml': API, 'ws/api.raml': API + 'uses:\n  up: ../secret.raml\n'})
        code = main(['validate', '-w', str(root / 'ws'), str(root / 'ws' / 'api.raml')])
        assert code == EXIT_INVALID
        assert 'workspace' in capsys.readouterr().err.lower()

    def test_no_workspace_guard_allows_the_same_include(self, workspace):
        root = workspace({'lib.raml': '#%RAML 1.0 Library\n', 'ws/api.raml': API + 'uses:\n  up: ../lib.raml\n'})
        argv = ['validate', '--no-workspace-guard', '-w', str(root / 'ws'), str(root / 'ws' / 'api.raml')]
        assert main(argv) == EXIT_OK

    def test_remote_without_a_client_says_so(self, files, monkeypatch):
        """`-r` needs httpx or requests; pyRAML depends on neither."""
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name in ('httpx', 'requests'):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', refuse)
        with pytest.raises(SystemExit) as caught:
            main(['validate', '-r', files('good.raml')])
        assert 'httpx or requests' in str(caught.value)


class TestUsage:
    def test_a_missing_subcommand_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as caught:
            main([])
        assert caught.value.code == 2

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as caught:
            main(['--version'])
        assert caught.value.code == 0
        assert 'pyraml' in capsys.readouterr().out
