"""The `fastraml` console script — docs/13-public-api.md section 8.

Exit codes and output shape, not parsing. Nothing in `cli.py` decides what is
valid, so the assertions here are about the contract a shell script or a CI job
depends on: what the exit code means, which stream each thing goes to, and that
`--json` stays diffable against go-raml's output
(docs/14 section 1.3).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fastraml.cli import EXIT_INVALID, EXIT_OK, main

API = '#%RAML 1.0\ntitle: Demo\n'
GOOD = API + 'types:\n  T:\n    type: string\n    minLength: 2\n/things:\n  get:\n'
BAD = API + 'types:\n  T:\n    type: integer\n    example: nope\n'


def test_openapi_export_supports_json(files, capsys):
    assert main(['openapi', '--format', 'json', files('good.raml')]) == EXIT_OK
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document['openapi'] == '3.0.3'
    assert '/things' in document['paths']
    assert captured.err == ''


def test_openapi_export_defaults_to_yaml(files, capsys):
    assert main(['openapi', files('good.raml')]) == EXIT_OK
    captured = capsys.readouterr()
    document = yaml.safe_load(captured.out)
    assert document['info']['title'] == 'Demo'
    assert captured.err == ''


def test_openapi_export_writes_a_file_with_o(files, tmp_path, capsys):
    target = tmp_path / 'api.yaml'
    assert main(['openapi', '-o', str(target), files('good.raml')]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ''
    document = yaml.safe_load(target.read_text(encoding='utf-8'))
    assert document['info']['title'] == 'Demo'
    assert '/things' in document['paths']
    assert b'\r' not in target.read_bytes()


@pytest.mark.parametrize('verb', ['tree', 'graph', 'openapi'])
def test_every_document_verb_writes_a_file_with_o(verb, files, tmp_path, capsys, monkeypatch):
    """`-o` on every verb whose output is a document, not just `openapi`.

    A shell redirect writes CRLF on Windows, which is how committed output comes
    to differ from what CI regenerates -- and `tree` is the verb whose output
    this repository commits.
    """
    monkeypatch.chdir(tmp_path)
    target = tmp_path / f'{verb}.out'
    assert main([verb, '-o', str(target), files('good.raml')]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ''
    assert target.read_bytes()
    assert b'\r' not in target.read_bytes()


@pytest.mark.parametrize('verb', ['tree', 'graph'])
def test_a_document_verb_without_o_still_prints(verb, files, capsys):
    assert main([verb, files('good.raml')]) == EXIT_OK
    assert capsys.readouterr().out.strip()


def test_openapi_export_o_reports_an_unwritable_file(files, tmp_path, capsys):
    assert main(['openapi', '-o', str(tmp_path / 'absent' / 'api.yaml'), files('good.raml')]) == EXIT_INVALID
    captured = capsys.readouterr()
    assert 'absent' in captured.err
    assert captured.out == ''


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

    def test_the_refusal_names_the_flag_and_the_root_that_would_work(self, workspace, capsys):
        """The root defaults to the entry file's directory, so this is the first
        thing anyone hits on an API whose libraries sit beside it rather than
        beneath it -- and the refusal alone does not say which flag widens it.
        """
        root = workspace({'secret.raml': API, 'ws/api.raml': API + 'uses:\n  up: ../secret.raml\n'})
        assert main(['validate', str(root / 'ws' / 'api.raml')]) == EXIT_INVALID
        err = capsys.readouterr().err
        assert 'pass -w ' in err
        assert str(root) in err.split('pass -w ', 1)[1]

    def test_no_hint_where_widening_would_not_have_helped(self, files, capsys):
        """A document that parses has nothing to suggest, and says nothing."""
        assert main(['validate', files('bad.raml')]) == EXIT_INVALID
        assert 'pass -w ' not in capsys.readouterr().err

    def test_no_workspace_guard_allows_the_same_include(self, workspace):
        root = workspace({'lib.raml': '#%RAML 1.0 Library\n', 'ws/api.raml': API + 'uses:\n  up: ../lib.raml\n'})
        argv = ['validate', '--no-workspace-guard', '-w', str(root / 'ws'), str(root / 'ws' / 'api.raml')]
        assert main(argv) == EXIT_OK

    def test_remote_without_a_client_says_so(self, files, monkeypatch):
        """`-r` needs httpx or requests; fastRAML depends on neither."""
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name in ('httpx', 'requests'):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', refuse)
        with pytest.raises(SystemExit) as caught:
            main(['validate', '-r', files('good.raml')])
        # Names the extra, not just the two libraries: "install httpx" left a
        # reader to work out that the package declares one.
        assert 'fastraml[http]' in str(caught.value)


class TestUsage:
    def test_import_does_not_load_a_parser_or_graph(self):
        code = (
            'import fastraml.cli, sys; '
            "unexpected = {'fastraml.parser.entry', 'fastraml.views.graph', 'yaml'} & sys.modules.keys(); "
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
        assert 'fastraml' in capsys.readouterr().out


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
        [('turtle', '@prefix raml:'), ('nt', '<fastraml://id'), ('dot', 'digraph raml {'), ('json', '"nodes"')],
    )
    def test_each_format_emits_its_own_syntax(self, graphed, capsys, form, marker):
        assert main(['graph', '--format', form, graphed]) == EXIT_OK
        assert marker in capsys.readouterr().out

    def test_the_default_format_is_turtle(self, graphed, capsys):
        assert main(['graph', graphed]) == EXIT_OK
        assert '@prefix raml:' in capsys.readouterr().out

    def test_tree_prints_the_whole_document_as_json(self, graphed, capsys):
        user = json.loads(self._tree(graphed, capsys))['types']['g.raml']['User']
        assert user['properties']['name']['type']['type'] == 'string'
        # Unwrapped, so the inherited property is present as well as the link.
        assert 'id' in user['properties']
        assert user['inherits'] == [{'$ref': 'fastraml://id#/declarations/types/Entity'}]

    def test_tree_prints_declarations_in_the_order_they_were_written(self, graphed, capsys):
        # docs/02 section 4: declaration order is preserved everywhere the model
        # is exposed. `build_tree` preserved it and the verb sorted the keys on
        # the way out, which is the same loss one step later -- and invisible to
        # a test of `build_tree`.
        tree = json.loads(self._tree(graphed, capsys))
        assert list(tree['types']['g.raml']) == ['Entity', 'User']
        assert list(tree['types']['g.raml']['User']['properties']) == ['name', 'id']

    def test_tree_addresses_agree_with_the_ones_graph_prints(self, graphed, capsys):
        # The counterpart claim in docs/16 § 11: one walk assigns both, so an
        # address read from a tree names a node in the graph. Only a test across
        # the two verbs catches them drifting apart.
        tree = json.loads(self._tree(graphed, capsys))
        assert main(['graph', '--format', 'json', graphed]) == EXIT_OK
        iris = {node['iri'] for node in json.loads(capsys.readouterr().out)['nodes']}
        addressed = tree['types']['g.raml']['User']
        assert addressed['id'] in iris
        assert addressed['inherits'][0]['$ref'] in iris

    def test_tree_positions_reports_a_span_per_declaration(self, graphed, capsys):
        assert main(['tree', '--positions', graphed]) == EXIT_OK
        spans = json.loads(capsys.readouterr().out)
        assert set(spans['g.raml']) == {'Entity', 'User'}
        assert all({'key', 'value'} <= set(span) for span in spans['g.raml'].values()), spans

    @staticmethod
    def _tree(path, capsys):
        assert main(['tree', path]) == EXIT_OK
        return capsys.readouterr().out

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


class TestServe:
    """docs/13 section 8. `serve` hands the `tree` projection to the viewer.

    The HTTP side is `contrib/fastraml-viewer`'s subject, with its own gate;
    what is pinned here is the wiring — the right document on the right socket —
    and that both failures are reported before any socket exists.
    """

    def test_an_unparseable_file_exits_one_before_anything_is_bound(self, files, capsys):
        """An occupied port is the detector: if the verb reached the bind, the
        error would name the socket rather than the file."""
        import socket

        blocker = socket.socket()
        blocker.bind(('127.0.0.1', 0))
        try:
            assert main(['serve', '--port', str(blocker.getsockname()[1]), files('absent.raml')]) == EXIT_INVALID
        finally:
            blocker.close()
        err = capsys.readouterr().err
        assert 'load resource' in err
        assert 'viewer:' not in err

    def test_it_passes_the_tree_projection_to_the_viewer(self, files, capsys, monkeypatch):
        pytest.importorskip('fastraml_viewer', reason='serve is an optional extra (docs/17 section 2)')
        import fastraml_viewer

        captured: dict[str, object] = {}

        class _FakeServer:
            server_address = ('127.0.0.1', 8123)

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def server_close(self) -> None:
                pass

        def _fake_serve(document, *, host, port):
            captured.update(document=document, host=host, port=port)
            return _FakeServer()

        monkeypatch.setattr(fastraml_viewer, 'serve', _fake_serve)
        assert main(['serve', '--port', '8123', files('good.raml')]) == EXIT_OK

        # The same projection the `tree` verb prints, not a re-parse under
        # different options: `validate` is off, as on every view verb.
        from fastraml import ParseOptions, parse_from_path
        from fastraml.views.tree import build_tree

        expected = build_tree(parse_from_path(files('good.raml'), ParseOptions(unwrap=True, validate=False)))
        assert captured['document'] == expected
        assert (captured['host'], captured['port']) == ('127.0.0.1', 8123)
        assert 'viewer: http://127.0.0.1:8123/' in capsys.readouterr().err

    def test_a_missing_package_is_named_not_traced(self, files, capsys, monkeypatch):
        monkeypatch.setitem(sys.modules, 'fastraml_viewer', None)
        assert main(['serve', files('good.raml')]) == EXIT_INVALID
        err = capsys.readouterr().err
        assert 'fastraml-viewer' in err
        assert 'Traceback' not in err

    def test_importing_the_cli_does_not_need_the_viewer(self):
        """The verb is the only importer, and it imports inside itself — the
        same shape as the `pyoxigraph` check in `tests/unit/test_queries.py`."""
        done = subprocess.run(
            [sys.executable, '-c', 'import fastraml.cli, sys; assert "fastraml_viewer" not in sys.modules'],
            capture_output=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr.decode()


class TestListVerb:
    """docs/13 § 8.1. The inventory — what `refs`, `deps` and `show` accept.

    Before it existed, learning a name meant `graph --format json` piped through
    a filter, a SPARQL query needing an optional dependency, or guessing.
    """

    def test_it_names_declarations_and_endpoints(self, graphed, capsys):
        assert main(['list', graphed]) == EXIT_OK
        out = capsys.readouterr().out
        assert 'User' in out
        assert 'Entity' in out
        assert '/users' in out

    def test_each_row_carries_a_position_to_go_to(self, graphed, capsys):
        main(['list', graphed])
        assert 'g.raml:' in capsys.readouterr().out

    def test_a_pattern_filters_by_name_case_insensitively(self, graphed, capsys):
        assert main(['list', graphed, 'user']) == EXIT_OK
        out = capsys.readouterr().out
        assert 'User' in out
        assert 'Entity' not in out

    def test_kind_narrows_and_is_repeatable(self, graphed, capsys):
        assert main(['list', graphed, '--kind', 'EndPoint']) == EXIT_OK
        out = capsys.readouterr().out
        assert '/users' in out
        assert 'Entity' not in out

    def test_it_does_not_list_the_nodes_inside_a_declaration(self, graphed, capsys):
        """Twenty to one on a real document, and reached by walking rather than
        by naming. Listing them would bury the answer in the question.
        """
        main(['list', graphed])
        assert 'Payload' not in capsys.readouterr().out

    def test_every_name_it_prints_can_be_passed_back(self, graphed, capsys):
        """The contract that makes it useful. A listed name that `show` then
        rejects would be worse than no listing.
        """
        main(['list', graphed, '--json'])
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows
        for row in rows:
            assert main(['show', graphed, row['name']]) == EXIT_OK, row['name']
            capsys.readouterr()

    def test_no_match_exits_one_and_says_so(self, graphed, capsys):
        assert main(['list', graphed, 'zzz']) == EXIT_INVALID
        assert 'nothing' in capsys.readouterr().err

    def test_json_is_one_object_per_entry(self, graphed, capsys):
        assert main(['list', graphed, '--json']) == EXIT_OK
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows
        assert all({'kind', 'name', 'iri', 'at'} <= row.keys() for row in rows)


class TestSuggestions:
    """A miss should not be a dead end — but it stays a miss."""

    def test_a_typo_is_offered_the_near_name(self, graphed, capsys):
        assert main(['refs', graphed, 'Usre']) == EXIT_INVALID
        err = capsys.readouterr().err
        assert 'did you mean' in err
        assert 'User' in err

    def test_a_remembered_fragment_is_offered_too(self, graphed, capsys):
        """`difflib` is ratio-based, so a short query inside a long name scores
        below any usable cutoff. That is the commonest miss there is.
        """
        assert main(['deps', graphed, 'Ent']) == EXIT_INVALID
        assert 'Entity' in capsys.readouterr().err

    def test_nothing_close_points_at_the_inventory(self, graphed, capsys):
        assert main(['show', graphed, 'zzzqqq']) == EXIT_INVALID
        err = capsys.readouterr().err
        assert 'did you mean' not in err
        assert 'fastraml list' in err

    def test_the_near_name_is_suggested_and_not_run(self, graphed, capsys):
        """Substituting answers a question the caller did not ask — the same
        reason an ambiguous name is reported rather than picked from.
        """
        assert main(['refs', graphed, 'Usre']) == EXIT_INVALID
        assert '-returns->' not in capsys.readouterr().out


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
        from fastraml.views.graph import RAML_NS

        query = f'PREFIX raml: <{RAML_NS}> SELECT ?m WHERE {{ ?o a raml:Operation ; raml:method ?m }}'
        assert main(['query', graphed, '-q', query]) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'get'

    def test_an_ask_prints_a_boolean(self, graphed, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from fastraml.views.graph import RAML_NS

        assert main(['query', graphed, '-q', f'PREFIX raml: <{RAML_NS}> ASK {{ ?o a raml:Operation }}']) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'true'

    def test_a_query_can_come_from_a_file(self, graphed, workspace, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from fastraml.views.graph import RAML_NS

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
        from fastraml.views.queries import QUERIES

        assert main(['query', '--list']) == EXIT_OK
        out = capsys.readouterr().out
        for query in QUERIES.values():
            assert query.name in out
            assert query.question in out

    def test_show_prints_a_runnable_query(self, capsys):
        assert main(['query', '--show', 'endpoint-tree']) == EXIT_OK
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


class TestWalkBounding:
    """`refs` returns 10 503 routes for one type at benchmark scale. Unbounded
    output is the same as no output, so the flags are not a nicety.
    """

    def test_a_result_carries_the_position_to_go_to(self, graphed, capsys):
        """The graph has held this on every node all along; the route renderer
        dropped it, which left `refs` saying that something uses a type without
        saying where to look.
        """
        assert main(['refs', graphed, 'Entity']) == EXIT_OK
        assert re.search(r'g\.raml:\d+', capsys.readouterr().out)

    def test_kind_keeps_only_that_kind(self, graphed, capsys):
        assert main(['refs', graphed, 'Entity', '--kind', 'Operation']) == EXIT_OK
        kinds = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
        assert kinds == {'Operation'}

    def test_kind_is_repeatable(self, graphed, capsys):
        assert main(['refs', graphed, 'Entity', '--kind', 'Operation', '--kind', 'EndPoint']) == EXIT_OK
        kinds = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
        assert kinds == {'Operation', 'EndPoint'}

    def test_depth_bounds_the_route_length(self, graphed, capsys):
        assert main(['refs', graphed, 'Entity', '--depth', '1']) == EXIT_OK
        for line in capsys.readouterr().out.splitlines():
            assert line.count('->') <= 1, line

    def test_limit_truncates_and_says_so_on_stderr(self, graphed, capsys):
        assert main(['refs', graphed, 'Entity', '--limit', '1']) == EXIT_OK
        captured = capsys.readouterr()
        assert len(captured.out.splitlines()) == 1
        assert 'more' in captured.err

    def test_an_unlimited_run_says_nothing_on_stderr(self, graphed, capsys):
        assert main(['refs', graphed, 'Entity']) == EXIT_OK
        assert capsys.readouterr().err == ''

    def test_json_carries_the_position_too(self, graphed, capsys):
        assert main(['refs', graphed, 'Entity', '--json', '--limit', '1']) == EXIT_OK
        record = json.loads(capsys.readouterr().out.splitlines()[0])
        assert re.fullmatch(r'g\.raml:\d+', record['at'])


V1 = (
    API
    + """types:
  Order:
    type: object
    properties:
      id: string
      discount: number
/orders:
  get:
    responses:
      200:
        body:
          application/json: Order
"""
)
V2 = V1.replace('      discount: number\n', '')


@pytest.fixture
def versions(workspace):
    root = workspace({'v1.raml': V1, 'v2.raml': V2})
    return str(root / 'v1.raml'), str(root / 'v2.raml')


class TestCompat:
    """docs/13 § 8.2. The exit code is the contract a CI job depends on."""

    def test_a_breaking_change_exits_one(self, versions, capsys):
        assert main(['compat', *versions]) == EXIT_INVALID
        out = capsys.readouterr()
        assert '| `$.discount` | required `number` |' in out.out
        assert 'breaking change' in out.err

    def test_o_writes_the_report_and_still_exits_one(self, versions, tmp_path, capsys):
        """`compat` exits 1 by design, so a shell redirect leaves a failed command
        and no way to tell a report it wrote from one it did not. `-o` separates
        the two: the file is written, the verdict still reaches the exit code,
        and the count still reaches stderr.
        """
        target = tmp_path / 'report.md'

        assert main(['compat', '-o', str(target), *versions]) == EXIT_INVALID

        captured = capsys.readouterr()
        assert captured.out == ''
        assert 'breaking change' in captured.err
        assert '# API compatibility' in target.read_text(encoding='utf-8')
        assert b'\r' not in target.read_bytes(), 'LF on every platform, unlike a shell redirect'

    def test_o_reports_an_unwritable_file_rather_than_the_verdict(self, versions, tmp_path, capsys):
        """A report nobody could write is a failure of the command. Saying
        "N breaking changes" over it would bury that.
        """
        assert main(['compat', '-o', str(tmp_path / 'absent' / 'report.md'), *versions]) == EXIT_INVALID

        captured = capsys.readouterr()
        assert 'absent' in captured.err
        assert 'breaking change' not in captured.err

    def test_o_carries_json_too(self, versions, tmp_path, capsys):
        target = tmp_path / 'report.jsonl'

        assert main(['compat', '--json', '-o', str(target), *versions]) == EXIT_INVALID

        assert capsys.readouterr().out == ''
        records = [json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()]
        assert records
        assert all('impact' in record for record in records)

    def test_an_unchanged_document_exits_zero_and_says_nothing(self, versions, capsys):
        assert main(['compat', versions[0], versions[0]]) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == ''

    def test_a_safe_change_exits_zero(self, workspace, capsys):
        widened = V1.replace('      id: string', '      id: string\n      note?: string')
        root = workspace({'a.raml': V1, 'b.raml': widened})
        assert main(['compat', str(root / 'a.raml'), str(root / 'b.raml')]) == EXIT_OK
        assert '| `$.note` | optional `string` |' in capsys.readouterr().out

    def test_breaking_only_still_exits_one_but_prints_less(self, workspace, capsys):
        both = V2.replace('      id: string', '      id: string\n      note?: string')
        root = workspace({'a.raml': V1, 'b.raml': both})
        assert main(['compat', str(root / 'a.raml'), str(root / 'b.raml'), '--breaking-only']) == EXIT_INVALID
        out = capsys.readouterr().out
        assert '| `$.discount` | required `number` |' in out
        assert '| `$.note` | optional `string` |' not in out

    def test_severity_is_a_threshold_not_a_membership_test(self, workspace, capsys):
        """docs/13 § 8: `--severity S` means S *and everything worse*, on every
        verb that has it. This took a repeatable exact set until `lint` arrived
        with a threshold and one flag name meant two things in one tool.
        """
        both = V2.replace('      id: string', '      id: string\n      note?: string')
        root = workspace({'a.raml': V1, 'b.raml': both})
        args = ['compat', str(root / 'a.raml'), str(root / 'b.raml')]

        assert main([*args, '--severity', 'compatible']) == EXIT_INVALID
        widened = capsys.readouterr().out
        # `compatible` selects compatible and worse, so the breaking change remains.
        assert '| `$.discount` | required `number` |' in widened
        assert '| `$.note` | optional `string` |' in widened

        assert main([*args, '--severity', 'breaking']) == EXIT_INVALID
        narrowed = capsys.readouterr().out
        assert '| `$.discount` | required `number` |' in narrowed
        assert '| `$.note` | optional `string` |' not in narrowed

    def test_breaking_only_is_the_top_of_that_scale(self, workspace, capsys):
        """It is `--severity breaking` said shorter, and kept because it is what
        a CI gate reaches for."""
        both = V2.replace('      id: string', '      id: string\n      note?: string')
        root = workspace({'a.raml': V1, 'b.raml': both})
        args = [str(root / 'a.raml'), str(root / 'b.raml')]

        assert main(['compat', *args, '--breaking-only']) == EXIT_INVALID
        shorthand = capsys.readouterr().out
        assert main(['compat', *args, '--severity', 'breaking']) == EXIT_INVALID
        assert capsys.readouterr().out == shorthand

    def test_json_carries_the_rule_and_the_reason(self, versions, capsys):
        assert main(['compat', *versions, '--json']) == EXIT_INVALID
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        removal = next(r for r in records if r['rule'] == 'response-property-removed')
        assert removal['impact'] == 'breaking'
        assert removal['operation'] == {'path': '/orders', 'method': 'get'}
        assert removal['location'] == {'kind': 'ResponseBody', 'status': '200', 'media_type': 'application/json'}
        assert removal['path'] == [{'kind': 'PropertySegment', 'name': 'discount'}]

    def test_json_carries_the_side_of_each_effective_use(self, workspace, capsys):
        """The model walk reports request and response sites, not a synthetic
        declaration record whose direction had to be reconstructed from a graph.
        """
        both_ways = """#%RAML 1.0
title: T
types:
  Thing:
    type: object
    properties:
      a: string
/things:
  post:
    body:
      application/json: Thing
    responses:
      200:
        body:
          application/json: Thing
"""
        root = workspace({'a.raml': both_ways, 'b.raml': both_ways.replace('      a: string', '      a?: string')})
        main(['compat', str(root / 'a.raml'), str(root / 'b.raml'), '--json'])
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        required = [record for record in records if record['attribute'] == 'required']
        assert {record['location']['kind'] for record in required} == {'RequestBody', 'ResponseBody'}
        assert {record['impact'] for record in required} == {'compatible', 'breaking'}

    def test_json_writes_nothing_to_stderr(self, versions, capsys):
        """A consumer parses stdout; the summary must not corrupt it."""
        main(['compat', *versions, '--json'])
        assert capsys.readouterr().err == ''

    def test_an_unreadable_file_exits_one(self, versions, capsys):
        assert main(['compat', versions[0], 'no-such-file.raml']) == EXIT_INVALID
        assert 'invalid' in capsys.readouterr().err

    def test_a_location_is_emitted_directly_from_the_model_walk(self, versions, capsys):
        main(['compat', *versions])
        out = capsys.readouterr().out
        assert '## `GET /orders`' in out
        assert '| `200` body `application/json` | `$.discount` |' in out
        assert 'fastraml://id' not in out


class TestEveryListedNameIsUsable:
    """The `list` -> `show` contract, checked against what a real document holds.

    The earlier version of this passed on a fixture with no traits and whose
    operations happened to render under a path. On a real API 215 of 525 listed
    names did not round-trip: `show <operation>` emitted a bare `:` -- not
    loadable YAML -- and traits and security schemes were refused outright.
    """

    RICH = (
        API
        + """securitySchemes:
  key:
    type: Pass Through
    describedBy:
      headers:
        X-Key: string
traits:
  paged:
    queryParameters:
      offset?: integer
types:
  Item:
    type: object
    properties:
      sku: string
/items:
  get:
    is: [paged]
    securedBy: [key]
    responses:
      200:
        body:
          application/json: Item
"""
    )

    @pytest.fixture
    def rich(self, workspace):
        return str(workspace({'api.raml': self.RICH}) / 'api.raml')

    def test_an_operation_renders_under_its_resource(self, rich, capsys):
        """It has no `path` of its own; the owning endpoint does."""
        assert main(['show', rich, 'get']) == EXIT_OK
        loaded = yaml.safe_load(capsys.readouterr().out)
        assert list(loaded) == ['/items']

    def test_every_listed_name_either_renders_or_explains_itself(self, rich, capsys):
        main(['list', rich, '--json'])
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows
        for row in rows:
            code = main(['show', rich, row['name']])
            out, err = capsys.readouterr()
            if code == EXIT_OK:
                assert yaml.safe_load(out), f'{row["kind"]} {row["name"]} rendered unloadable YAML'
            else:
                assert 'no effective view' in err, f'{row["kind"]} {row["name"]}: unhelpful refusal'

    def test_a_refused_kind_names_where_it_was_written(self, rich, capsys):
        assert main(['show', rich, 'paged']) == EXIT_INVALID
        err = capsys.readouterr().err
        assert 'api.raml:' in err
        assert 'fastraml refs paged' in err


class TestDepsWorksOnMoreThanTypes:
    def test_an_endpoint_is_made_of_its_operations(self, graphed, capsys):
        """`deps` walked the type closure only, so every endpoint and every
        operation in a document reported "nothing found".
        """
        assert main(['deps', graphed, '/users']) == EXIT_OK
        assert 'supportedOperation' in capsys.readouterr().out

    def test_a_type_still_walks_the_type_closure(self, graphed, capsys):
        """Widening `deps` for a type would pull in its use sites, which is
        `refs`'s question, not this one.
        """
        main(['deps', graphed, 'User'])
        out = capsys.readouterr().out
        assert '-inherits-> Entity' in out
        assert 'supportedOperation' not in out


class TestResultsAreBounded:
    def test_refs_stops_at_a_default_and_says_so(self, graphed, capsys, monkeypatch):
        monkeypatch.setattr('fastraml.cli._DEFAULT_LIMIT', 1)
        assert main(['refs', graphed, 'User']) == EXIT_OK
        out, err = capsys.readouterr()
        assert len(out.splitlines()) == 1
        assert 'more' in err, 'the remainder must be reported'

    def test_the_note_goes_to_stderr_so_a_pipe_is_clean(self, graphed, capsys, monkeypatch):
        monkeypatch.setattr('fastraml.cli._DEFAULT_LIMIT', 1)
        main(['refs', graphed, 'User'])
        assert 'more' not in capsys.readouterr().out

    def test_zero_still_means_all(self, graphed, capsys, monkeypatch):
        monkeypatch.setattr('fastraml.cli._DEFAULT_LIMIT', 1)
        main(['refs', graphed, 'User', '--limit', '0'])
        assert len(capsys.readouterr().out.splitlines()) > 1


class TestSkillsVerb:
    """The served agent guides (docs/13 section 8.3).

    The point of the verb is that an installed skill can be a *stub*: the guide
    an agent reads ships with the build that answers it, so it cannot go stale.
    That only holds if the data is present and the stub's own commands work, so
    these tests name both.
    """

    def test_the_guides_ship_inside_the_package(self):
        """Not beside the repository. A wheel with no `skilldata/` serves an
        empty listing, and the stub it backs then points at nothing.
        """
        from fastraml.cli import _skill_root

        root = _skill_root()
        assert root.is_dir(), f'{root} is missing from the installed package'
        assert (root / 'core' / 'SKILL.md').is_file()

    def test_list_names_every_guide(self, capsys):
        assert main(['skills', 'list']) == EXIT_OK
        out = capsys.readouterr().out
        for name in ('core', 'raml', 'backward', 'sparql'):
            assert name in out

    def test_the_hint_goes_to_stderr_so_a_pipe_is_clean(self, capsys):
        main(['skills', 'list'])
        captured = capsys.readouterr()
        assert 'skills get' in captured.err
        assert 'skills get' not in captured.out

    def test_get_prints_the_guide_with_its_frontmatter(self, capsys):
        """Frontmatter included: the output is a valid SKILL.md, so an agent can
        write it straight to disk as a skill of its own.
        """
        assert main(['skills', 'get', 'core']) == EXIT_OK
        out = capsys.readouterr().out
        assert out.startswith('---\n')
        assert yaml.safe_load(out.split('---')[1])['name'] == 'core'

    def test_every_guide_a_served_guide_points_at_resolves(self, capsys):
        """Every `fastraml skills get X` any served guide tells an agent to run.

        A guide that points at one this build does not serve is the failure the
        whole pattern exists to prevent. The names are **read out of the guides**
        rather than listed here: a hardcoded list passes while a new guide goes
        unmentioned, which is exactly how `lint` was missed.
        """
        root = Path('fastraml/skilldata')
        referenced = set()
        for guide in sorted(root.glob('*/SKILL.md')):
            referenced |= set(re.findall(r'fastraml skills get ([a-z-]+)', guide.read_text(encoding='utf-8')))
        assert referenced, 'no guide references another'
        for name in sorted(referenced):
            assert main(['skills', 'get', name]) == EXIT_OK, name
            assert capsys.readouterr().out.strip()

    def test_every_catalogue_name_a_guide_cites_still_exists(self, capsys):
        """A guide naming a query or rule this build does not ship.

        The catalogue is not frozen — eight queries became lint rules and left
        it — and a guide that still cites one teaches an agent a command that
        exits 1. Both guides went stale that way at once.
        """
        root = Path('fastraml/skilldata')
        text = '\n'.join(guide.read_text(encoding='utf-8') for guide in sorted(root.glob('**/*.md')))

        assert main(['query', '--list']) == EXIT_OK
        queries = {line.split()[0] for line in capsys.readouterr().out.splitlines() if line[:1].isalpha()}
        assert main(['lint', '--list-rules']) == EXIT_OK
        rules = {line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()}

        cited = set(re.findall(r'query .*?(?:--show|-n) ([a-z][a-z-]+)', text))
        cited |= set(re.findall(r'lint --explain ([a-z][a-z-]+)', text))
        assert cited, 'no guide cites a catalogue entry'
        missing = cited - queries - rules
        assert missing == set(), missing

    def test_every_served_guide_is_reachable_from_the_stub(self, capsys):
        """The stub is the only file an agent installs, so a guide it never
        names is a guide nobody loads."""
        assert main(['skills', 'list']) == EXIT_OK
        served = {line.split()[0] for line in capsys.readouterr().out.splitlines() if line and not line.startswith(' ')}
        served.discard('Read')
        stub = Path('fastraml/skilldata/fastraml/SKILL.md').read_text(encoding='utf-8')
        core = Path('fastraml/skilldata/core/SKILL.md').read_text(encoding='utf-8')
        unreachable = {name for name in served if f'skills get {name}' not in stub + core}
        assert unreachable == set(), unreachable

    def test_full_appends_the_reference_files(self, capsys):
        plain = main(['skills', 'get', 'core'])
        short = len(capsys.readouterr().out)
        assert plain == EXIT_OK
        assert main(['skills', 'get', 'core', '--full']) == EXIT_OK
        out = capsys.readouterr().out
        assert len(out) > short
        assert 'references/commands.md' in out

    def test_the_language_guide_carries_both_its_tables(self, capsys):
        """`raml` splits the spec's tables out so the body stays loadable.

        A body that names a reference this build does not ship sends an agent
        to a file that is not there, which is the stub's failure one level down.
        """
        assert main(['skills', 'get', 'raml', '--full']) == EXIT_OK
        out = capsys.readouterr().out
        assert 'references/facets.md' in out
        assert 'references/nodes.md' in out

    def test_several_guides_are_separated(self, capsys):
        assert main(['skills', 'get', 'backward', 'sparql']) == EXIT_OK
        assert '\n---\n' in capsys.readouterr().out

    def test_backward_is_routed_by_task_not_by_the_verb_or_the_module(self, capsys):
        """The guide is named for the question an agent is asking -- "is this
        backward compatible?" -- and not for `compat`, the verb that answers it,
        nor for `views/backward/`, the package behind it. It still has to *name*
        the verb, or an agent that finds the guide cannot run anything.
        """
        root = Path('fastraml/skilldata')
        source = (root / 'backward' / 'SKILL.md').read_text(encoding='utf-8')
        front = yaml.safe_load(source.split('---')[1])
        assert 'backward compatibility' in front['description']
        assert 'fastraml compat' in front['description']
        assert not (root / 'compat' / 'SKILL.md').exists()
        assert 'fastraml diff' not in source

        assert main(['skills', 'get', 'backward']) == EXIT_OK
        rendered = capsys.readouterr().out
        assert yaml.safe_load(rendered.split('---')[1])['name'] == 'backward'
        assert '`api-schema`' in rendered
        assert 'PatternPropertySegment' in rendered
        assert {'breaking', 'review', 'compatible', 'cosmetic'} <= set(re.findall(r'`([a-z]+)`', rendered))

    def test_core_does_not_teach_the_retired_graph_diff_report(self):
        core = Path('fastraml/skilldata/core/SKILL.md').read_text(encoding='utf-8')
        commands = Path('fastraml/skilldata/core/references/commands.md').read_text(encoding='utf-8')
        assert 'grouped by rule' not in core
        assert 'source-file addresses' in core
        assert '`risky`, `safe`' not in commands
        assert '`breaking`, `review`, `compatible`, `cosmetic`' in commands

    def test_an_unknown_guide_is_named_not_guessed(self, capsys):
        """As `_resolve` refuses to pick between ambiguous nodes: printing the
        wrong guide answers a question nobody asked.
        """
        assert main(['skills', 'get', 'cor']) == EXIT_INVALID
        captured = capsys.readouterr()
        assert 'no such guide' in captured.err
        assert not captured.out

    def test_get_without_a_name_lists_what_it_wanted(self, capsys):
        assert main(['skills', 'get']) == EXIT_INVALID
        assert 'core' in capsys.readouterr().err

    def test_json_carries_the_content(self, capsys):
        assert main(['skills', 'get', 'core', '--json']) == EXIT_OK
        record = json.loads(capsys.readouterr().out.strip())
        assert record['name'] == 'core'
        assert 'fastraml' in record['content']

    def test_every_guide_is_a_valid_agent_skill(self):
        """Name, description and the directory name agree, per the Agent Skills
        specification, so a guide can also be installed directly rather than
        served.

        Every directory is required to hold one, with no skip: a folder under
        `skilldata/` without a `SKILL.md` is the broken install `_guides` walks
        straight past, and this is the only thing that would notice.
        """
        from fastraml.cli import _skill_root

        for folder in sorted(_skill_root().iterdir()):
            skill = folder / 'SKILL.md'
            assert skill.is_file(), f'{folder.name} ships no SKILL.md'
            front = yaml.safe_load(skill.read_text(encoding='utf-8').split('---')[1])
            assert front['name'] == folder.name
            assert 0 < len(front['description']) <= 1024


class TestSkillsInstall:
    """Installing a guide into a skills directory (docs/13 section 8.3).

    Built in rather than delegated to `gh skill`: that tool is third-party, in
    preview, and not guaranteed present, and the operation is a file copy into a
    documented directory.
    """

    def test_the_default_target_is_the_cross_client_directory(self, tmp_path, capsys, monkeypatch):
        """`.agents/skills`, not `.claude/skills`. The specification names the
        first as the interoperability path and three harnesses scan it, so one
        copy serves every agent rather than one copy per agent.
        """
        monkeypatch.chdir(tmp_path)
        assert main(['skills', 'install']) == EXIT_OK
        assert (tmp_path / '.agents' / 'skills' / 'fastraml' / 'SKILL.md').is_file()
        assert 'installed' in capsys.readouterr().out

    def test_it_installs_the_stub_by_default_not_a_guide(self, tmp_path, capsys, monkeypatch):
        """The stub is what an agent needs; the guides are what the stub fetches.

        Installing `core` by default would defeat the whole arrangement -- the
        copy would go stale, which is what serving from the package prevents.
        """
        monkeypatch.chdir(tmp_path)
        main(['skills', 'install'])
        body = (tmp_path / '.agents' / 'skills' / 'fastraml' / 'SKILL.md').read_text(encoding='utf-8')
        assert 'skills get core' in body, 'the installed skill must bootstrap from the CLI'

    def test_user_scope_writes_under_home(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
        assert main(['skills', 'install', '--user']) == EXIT_OK
        assert (tmp_path / '.agents' / 'skills' / 'fastraml' / 'SKILL.md').is_file()

    def test_dir_overrides_both_scopes(self, tmp_path, capsys):
        assert main(['skills', 'install', '--dir', str(tmp_path / 'own')]) == EXIT_OK
        assert (tmp_path / 'own' / 'fastraml' / 'SKILL.md').is_file()

    def test_a_named_guide_installs_too(self, tmp_path, capsys):
        assert main(['skills', 'install', 'core', 'backward', '--dir', str(tmp_path)]) == EXIT_OK
        assert (tmp_path / 'core' / 'SKILL.md').is_file()
        assert (tmp_path / 'backward' / 'SKILL.md').is_file()

    def test_it_refuses_to_replace_without_force(self, tmp_path, capsys):
        """An installed skill is a file the user may have edited."""
        assert main(['skills', 'install', '--dir', str(tmp_path)]) == EXIT_OK
        capsys.readouterr()
        assert main(['skills', 'install', '--dir', str(tmp_path)]) == EXIT_INVALID
        assert 'force' in capsys.readouterr().err

    def test_force_replaces(self, tmp_path, capsys):
        main(['skills', 'install', '--dir', str(tmp_path)])
        target = tmp_path / 'fastraml' / 'SKILL.md'
        target.write_text('edited', encoding='utf-8')
        assert main(['skills', 'install', '--dir', str(tmp_path), '--force']) == EXIT_OK
        assert target.read_text(encoding='utf-8') != 'edited'

    def test_nothing_is_written_when_one_name_collides(self, tmp_path, capsys):
        """Every collision is checked before the first write, or a failed install
        leaves the user to work out which names landed.
        """
        (tmp_path / 'backward').mkdir()
        (tmp_path / 'backward' / 'SKILL.md').write_text('mine', encoding='utf-8')
        assert main(['skills', 'install', 'core', 'backward', '--dir', str(tmp_path)]) == EXIT_INVALID
        assert not (tmp_path / 'core').exists(), 'a refused install must write nothing at all'

    def test_an_unknown_guide_is_refused(self, tmp_path, capsys):
        assert main(['skills', 'install', 'nope', '--dir', str(tmp_path)]) == EXIT_INVALID
        assert 'no such guide' in capsys.readouterr().err

    def test_the_stub_is_hidden_from_the_listing(self, capsys):
        """Still gettable and installable by name -- a listing of documentation
        should not advertise the shim whose only job is to fetch it.
        """
        main(['skills', 'list'])
        # The name column, not the whole line: every guide's *description*
        # mentions `fastraml`, so a substring test over the output passes and
        # fails for reasons that have nothing to do with hiding.
        listed = [line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert 'fastraml' not in listed
        assert listed == ['backward', 'core', 'lint', 'raml', 'sparql']
        assert main(['skills', 'get', 'fastraml']) == EXIT_OK
        assert capsys.readouterr().out.strip()

    def test_the_installed_stub_is_a_valid_agent_skill(self, tmp_path):
        """Name matches its directory, per the Agent Skills specification, so
        every client that scans the directory accepts it.
        """
        main(['skills', 'install', '--dir', str(tmp_path)])
        front = yaml.safe_load((tmp_path / 'fastraml' / 'SKILL.md').read_text(encoding='utf-8').split('---')[1])
        assert front['name'] == 'fastraml'
        assert 0 < len(front['description']) <= 1024

    def test_the_repo_stub_matches_the_one_the_cli_serves(self):
        """`skills/fastraml/` exists for installers that read the repository --
        `gh skill install`, or a plugin manifest. `fastraml/skilldata/fastraml/`
        is what `skills install` writes. Two copies drift, so they are pinned
        byte-identical: the stub carries no serving-only frontmatter, because
        what `install` writes is what a client reads.
        """
        from fastraml.cli import _skill_root

        served = (_skill_root() / 'fastraml' / 'SKILL.md').read_text(encoding='utf-8')
        committed = Path('skills/fastraml/SKILL.md').read_text(encoding='utf-8')
        assert served == committed, 'skills/fastraml/SKILL.md is stale against fastraml/skilldata/fastraml/SKILL.md'
