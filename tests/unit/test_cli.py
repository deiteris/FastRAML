"""The `pyraml` console script — docs/13-public-api.md section 8.

Exit codes and output shape, not parsing. Nothing in `cli.py` decides what is
valid, so the assertions here are about the contract a shell script or a CI job
depends on: what the exit code means, which stream each thing goes to, and that
`--json` stays diffable against the reference implementation's output
(docs/14 section 1.3).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
import yaml

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
        # Names the extra, not just the two libraries: "install httpx" left a
        # reader to work out that the package declares one.
        assert 'pyraml[http]' in str(caught.value)


class TestUsage:
    def test_import_does_not_load_a_parser_or_graph(self):
        code = (
            'import pyraml.cli, sys; '
            "unexpected = {'pyraml.parser.entry', 'pyraml.views.graph', 'yaml'} & sys.modules.keys(); "
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

    def test_tree_prints_the_whole_document_as_json(self, graphed, capsys):
        user = json.loads(self._tree(graphed, capsys))['types']['g.raml']['User']
        assert user['properties']['name']['type']['type'] == 'string'
        # Unwrapped, so the inherited property is present as well as the link.
        assert 'id' in user['properties']
        assert user['inherits'] == [{'$ref': 'pyraml://id#/declarations/types/Entity'}]

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
        assert 'pyraml list' in err

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
        from pyraml.views.graph import RAML_NS

        query = f'PREFIX raml: <{RAML_NS}> SELECT ?m WHERE {{ ?o a raml:Operation ; raml:method ?m }}'
        assert main(['query', graphed, '-q', query]) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'get'

    def test_an_ask_prints_a_boolean(self, graphed, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from pyraml.views.graph import RAML_NS

        assert main(['query', graphed, '-q', f'PREFIX raml: <{RAML_NS}> ASK {{ ?o a raml:Operation }}']) == EXIT_OK
        assert capsys.readouterr().out.strip() == 'true'

    def test_a_query_can_come_from_a_file(self, graphed, workspace, capsys):
        pytest.importorskip('pyoxigraph', reason='SPARQL is an optional extra (docs/16 section 5.1)')
        from pyraml.views.graph import RAML_NS

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
        from pyraml.views.queries import QUERIES

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


class TestDiff:
    """docs/13 § 8.2. The exit code is the contract a CI job depends on."""

    def test_a_breaking_change_exits_one(self, versions, capsys):
        assert main(['diff', *versions]) == EXIT_INVALID
        out = capsys.readouterr()
        assert 'response-property-removed' in out.out
        assert 'breaking change' in out.err

    def test_an_unchanged_document_exits_zero_and_says_nothing(self, versions, capsys):
        assert main(['diff', versions[0], versions[0]]) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == ''

    def test_a_safe_change_exits_zero(self, workspace, capsys):
        widened = V1.replace('      id: string', '      id: string\n      note?: string')
        root = workspace({'a.raml': V1, 'b.raml': widened})
        assert main(['diff', str(root / 'a.raml'), str(root / 'b.raml')]) == EXIT_OK
        assert 'response-property-added' in capsys.readouterr().out

    def test_breaking_only_still_exits_one_but_prints_less(self, workspace, capsys):
        both = V2.replace('      id: string', '      id: string\n      note?: string')
        root = workspace({'a.raml': V1, 'b.raml': both})
        assert main(['diff', str(root / 'a.raml'), str(root / 'b.raml'), '--breaking-only']) == EXIT_INVALID
        out = capsys.readouterr().out
        assert 'response-property-removed' in out
        assert 'safe' not in out

    def test_json_carries_the_rule_and_the_reason(self, versions, capsys):
        assert main(['diff', *versions, '--json']) == EXIT_INVALID
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        removal = next(r for r in records if r['rule'] == 'response-property-removed')
        assert removal['severity'] == 'breaking'
        assert removal['because']
        assert removal['directions'] == ['response']

    def test_json_carries_every_side_the_rule_was_graded_on(self, workspace, capsys):
        """Or the record contradicts itself.

        A type that is a POST body and a GET response is graded on the worse
        side. Writing one side put `direction: request` beside
        `rule: response-property-optional` in the same object, and a consumer
        regrading these facts its own way could not have reached the published
        answer from them.
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
        main(['diff', str(root / 'a.raml'), str(root / 'b.raml'), '--json'])
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        declared = next(r for r in records if '#/declarations/' in r['iri'])
        assert declared['directions'] == ['request', 'response']
        assert declared['severity'] == 'breaking'

    def test_json_writes_nothing_to_stderr(self, versions, capsys):
        """A consumer parses stdout; the summary must not corrupt it."""
        main(['diff', *versions, '--json'])
        assert capsys.readouterr().err == ''

    def test_an_unreadable_file_exits_one(self, versions, capsys):
        assert main(['diff', versions[0], 'no-such-file.raml']) == EXIT_INVALID
        assert 'invalid' in capsys.readouterr().err

    def test_a_location_reads_as_a_path_not_an_iri(self, versions, capsys):
        """The IRI is structural so a findable path can be recovered from it."""
        main(['diff', *versions])
        assert '/orders get -> 200 application/json .discount' in capsys.readouterr().out


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
        assert 'pyraml refs paged' in err


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
        monkeypatch.setattr('pyraml.cli._DEFAULT_LIMIT', 1)
        assert main(['refs', graphed, 'User']) == EXIT_OK
        out, err = capsys.readouterr()
        assert len(out.splitlines()) == 1
        assert 'more' in err, 'the remainder must be reported'

    def test_the_note_goes_to_stderr_so_a_pipe_is_clean(self, graphed, capsys, monkeypatch):
        monkeypatch.setattr('pyraml.cli._DEFAULT_LIMIT', 1)
        main(['refs', graphed, 'User'])
        assert 'more' not in capsys.readouterr().out

    def test_zero_still_means_all(self, graphed, capsys, monkeypatch):
        monkeypatch.setattr('pyraml.cli._DEFAULT_LIMIT', 1)
        main(['refs', graphed, 'User', '--limit', '0'])
        assert len(capsys.readouterr().out.splitlines()) > 1
