"""The `pyraml` console script — docs/13-public-api.md section 8.

Exit codes and output shape, not parsing. Nothing in `cli.py` decides what is
valid, so the assertions here are about the contract a shell script or a CI job
depends on: what the exit code means, which stream each thing goes to, and that
`--json` stays diffable against the reference implementation's output
(docs/14 section 1.3).
"""

from __future__ import annotations

import json
import subprocess
import sys

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
    def test_import_does_not_load_a_parser_or_graph(self):
        code = (
            'import pyraml.cli, sys; '
            "unexpected = {'pyraml.parser.entry', 'pyraml.graph', 'yaml'} & sys.modules.keys(); "
            'assert not unexpected, unexpected'
        )
        subprocess.run([sys.executable, '-c', code], check=True)  # noqa: S603 - this interpreter, fixed code

    def test_a_missing_subcommand_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as caught:
            main([])
        assert caught.value.code == 2

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as caught:
            main(['--version'])
        assert caught.value.code == 0
        assert 'pyraml' in capsys.readouterr().out


GRAPHED = (
    API
    + """types:
  Entity:
    type: object
    properties:
      id: string
  User:
    type: Entity
    properties:
      name: string
/users:
  get:
    responses:
      200:
        body:
          application/json:
            type: User
"""
)


@pytest.fixture
def graphed(workspace):
    return str(workspace({'g.raml': GRAPHED}) / 'g.raml')


class TestGraphVerbs:
    """docs/13 section 8.1. Presentation and exit codes, as above — what the
    graph *means* is `tests/unit/test_graph.py`'s subject, not this file's.
    """

    @pytest.mark.parametrize(
        ('form', 'marker'),
        [('turtle', '@prefix raml:'), ('nt', '<pyraml://id'), ('dot', 'digraph raml {'), ('json', '"nodes"')],
    )
    def test_each_format_emits_its_own_syntax(self, graphed, capsys, form, marker):
        assert main(['graph', '--format', form, graphed]) == EXIT_OK
        assert marker in capsys.readouterr().out

    def test_the_default_format_is_turtle(self, graphed, capsys):
        assert main(['graph', graphed]) == EXIT_OK
        assert '@prefix raml:' in capsys.readouterr().out

    def test_refs_reports_the_route_and_not_only_the_hit(self, graphed, capsys):
        assert main(['refs', graphed, 'User']) == EXIT_OK
        out = capsys.readouterr().out
        assert 'Operation' in out
        assert '-returns->' in out
        assert '-payload->' in out

    def test_deps_walks_the_other_way(self, graphed, capsys):
        assert main(['deps', graphed, 'User']) == EXIT_OK
        assert '-inherits-> Entity' in capsys.readouterr().out

    def test_json_output_is_one_object_per_result(self, graphed, capsys):
        assert main(['refs', graphed, 'User', '--json']) == EXIT_OK
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows
        assert all({'kind', 'iri', 'route'} <= row.keys() for row in rows)

    def test_an_unknown_name_exits_one(self, graphed, capsys):
        assert main(['refs', graphed, 'Nope']) == EXIT_INVALID
        assert 'no such node' in capsys.readouterr().err

    def test_a_multi_parent_type_does_not_make_its_parents_ambiguous(self, workspace, capsys):
        """The end-to-end form of the defect `Graph.find` now rules out.

        `Admin: [User, Entity]` builds a synthetic parent per branch carrying
        the parent's name, so `refs Entity` reported an ambiguity between two
        nodes that are the same type. It exited 1 on a three-type document.
        """
        path = str(
            workspace(
                {
                    'api.raml': API + 'types:\n'
                    '  Entity:\n    type: object\n    properties:\n      id: string\n'
                    '  User:\n    type: Entity\n    properties:\n      name: string\n'
                    '  Admin:\n    type: [User, Entity]\n    properties:\n      level: integer\n'
                    '/admins:\n  get:\n    responses:\n      200:\n        body:\n'
                    '          application/json:\n            type: Admin\n'
                }
            )
            / 'api.raml'
        )
        assert main(['refs', path, 'Entity']) == EXIT_OK
        out = capsys.readouterr()
        assert 'ambiguous' not in out.err
        assert 'Operation' in out.out, 'the walk should reach the operation that returns Admin'

    def test_an_invalid_document_exits_one(self, files, capsys):
        """A document that will not *parse* has no graph. One that merely fails
        validation does — the graph verbs run with `validate=False`.
        """
        assert main(['graph', files('nonexistent.raml')]) == EXIT_INVALID
        assert 'invalid' in capsys.readouterr().err

    def test_a_document_with_a_bad_example_still_graphs(self, files, capsys):
        assert main(['graph', files('bad.raml')]) == EXIT_OK
        assert '@prefix raml:' in capsys.readouterr().out


class TestQueryVerb:
    def test_without_a_store_it_says_so_and_exits_one(self, graphed, capsys, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == 'pyoxigraph':
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', refuse)
        assert main(['query', graphed, '-q', 'ASK {}']) == EXIT_INVALID
        assert 'install pyoxigraph' in capsys.readouterr().err

    def test_a_select_prints_a_row_per_solution(self, graphed, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from pyraml.graph import RAML_NS

        query = f'PREFIX raml: <{RAML_NS}> SELECT ?m WHERE {{ ?o a raml:Operation ; raml:method ?m }}'
        assert main(['query', graphed, '-q', query]) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'get'

    def test_an_ask_prints_a_boolean(self, graphed, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from pyraml.graph import RAML_NS

        assert main(['query', graphed, '-q', f'PREFIX raml: <{RAML_NS}> ASK {{ ?o a raml:Operation }}']) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'true'

    def test_a_query_can_come_from_a_file(self, graphed, workspace, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from pyraml.graph import RAML_NS

        path = workspace({'q.rq': f'PREFIX raml: <{RAML_NS}> ASK {{ ?o a raml:Api }}'}) / 'q.rq'
        assert main(['query', graphed, '-Q', str(path)]) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'true'

    def test_the_two_query_sources_are_mutually_exclusive(self, graphed):
        with pytest.raises(SystemExit) as caught:
            main(['query', graphed, '-q', 'ASK {}', '-Q', 'q.rq'])
        assert caught.value.code == 2


class TestQueryCatalogue:
    """docs/13 § 8.1 — `--list` and `--show` are text, so neither needs a store
    nor a file. That is the point of them: a user who has not installed
    `pyoxigraph` can still find out what the tool would ask.
    """

    def test_list_names_every_query_and_its_question(self, capsys):
        from pyraml.queries import QUERIES

        assert main(['query', '--list']) == EXIT_OK
        out = capsys.readouterr().out
        for query in QUERIES.values():
            assert query.name in out
            assert query.question in out

    def test_show_prints_a_runnable_query(self, capsys):
        assert main(['query', '--show', 'unused-types']) == EXIT_OK
        out = capsys.readouterr().out
        assert 'PREFIX raml:' in out
        assert 'SELECT' in out

    def test_an_unknown_name_exits_one(self, capsys):
        assert main(['query', '--show', 'nope']) == EXIT_INVALID
        assert 'try --list' in capsys.readouterr().err

    def test_a_named_query_runs(self, graphed, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        assert main(['query', graphed, '-n', 'endpoint-tree']) == EXIT_OK
        assert '/users' in capsys.readouterr().out

    def test_an_unknown_named_query_exits_one_before_parsing(self, capsys):
        assert main(['query', 'no-such-file.raml', '-n', 'nope']) == EXIT_INVALID
        err = capsys.readouterr().err
        assert 'try --list' in err
        assert 'invalid' not in err, 'the name is checked before the file is opened'

    def test_a_query_with_no_source_says_which_flags_exist(self, graphed, capsys):
        assert main(['query', graphed]) == EXIT_INVALID
        assert '-q, -Q or -n' in capsys.readouterr().err

    def test_a_query_with_no_file_says_so(self, capsys):
        assert main(['query', '-n', 'endpoint-tree']) == EXIT_INVALID
        assert 'needs a FILE' in capsys.readouterr().err
