"""The argument parser, and which module runs each verb.

Building the parser imports no verb module: `COMMANDS` names each handler as
`module:function` text, and `main` imports only the one it runs, so `--help`
and `--version` load no parser, view or YAML.
"""

from __future__ import annotations

import argparse
from typing import Final

from fastraml import __version__

#: How many routes `refs`/`deps` print by default. A widely used type can have
#: thousands; the count of the rest goes to stderr. `--limit 0` prints all.
_DEFAULT_LIMIT = 50

#: Where an installed skill goes: the Agent Skills cross-client directory, so
#: one copy serves every agent that scans it. `--dir` overrides it.
SKILL_DIR: Final = '.agents/skills'

#: Subcommand name to its handler, as `module:function` under `fastraml.cli`.
COMMANDS: Final = {
    'validate': 'check:_validate',
    'info': 'check:_info',
    'graph': 'export:_graph',
    'convert': 'export:_convert',
    'tree': 'export:_tree',
    'serve': 'serve:_serve',
    'lsp': 'serve:_lsp',
    'refs': 'navigate:_walk',
    'deps': 'navigate:_walk',
    'show': 'navigate:_show_type',
    'list': 'navigate:_list',
    'compat': 'compat:_compat',
    'query': 'query:_query',
    'lint': 'lint:_lint',
    'skills': 'skills:_skills',
    'join': 'join:_join',
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='fastraml', description='Parse and validate RAML 1.0.')
    parser.add_argument('--version', action='version', version=f'fastraml {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)

    validate = commands.add_parser('validate', help='parse, unwrap and validate one or more files')
    validate.add_argument('files', metavar='FILE', nargs='+')
    validate.add_argument('--json', action='store_true', help='emit one JSON object per file (JSON Lines)')
    validate.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help='report each file and its timing; twice for the backend and model counts',
    )
    _add_common(validate)

    info = commands.add_parser('info', help='backend, timings and model counts for one file')
    info.add_argument('files', metavar='FILE', nargs=1)
    _add_common(info)

    graph = commands.add_parser('graph', help='project the effective model as a graph')
    graph.add_argument('files', metavar='FILE', nargs=1)
    graph.add_argument(
        '--format',
        choices=('nt', 'turtle', 'dot', 'json'),
        default='turtle',
        help='N-Triples, Turtle, Graphviz, or plain JSON (default: turtle)',
    )
    _add_output(graph)
    _add_common(graph)

    _add_convert(commands)

    tree = commands.add_parser('tree', help='the effective document as an addressed JSON tree')
    tree.add_argument('files', metavar='FILE', nargs=1)
    tree.add_argument('--positions', action='store_true', help='the span of every declaration instead of the document')
    _add_output(tree)
    _add_common(tree)

    _add_serve(commands)
    _add_lsp(commands)
    _add_navigation(commands)

    _add_compat(commands)

    query = commands.add_parser('query', help='run SPARQL over the graph (needs pyoxigraph)')
    query.add_argument('files', metavar='FILE', nargs='*')
    source = query.add_mutually_exclusive_group()
    source.add_argument('-q', dest='sparql', help='the query text')
    source.add_argument('-Q', dest='query_file', help='a file holding the query')
    source.add_argument('-n', dest='named', metavar='NAME', help='a query from the catalogue (see --list)')
    query.add_argument('--list', dest='catalogue', action='store_true', help='list the catalogue and exit')
    query.add_argument('--show', metavar='NAME', help='print one catalogue query rather than running it')
    query.add_argument('--json', action='store_true', help='JSON rather than a table')
    _add_output(query)
    _add_common(query)

    _add_lint(commands)

    _add_skills(commands)

    _add_join(commands)

    return parser


def _add_convert(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    convert = commands.add_parser('convert', help='convert between RAML and other schema formats')
    targets = convert.add_subparsers(dest='target', required=True)
    openapi = targets.add_parser('openapi', help='export the effective API as OpenAPI 3.0.3')
    openapi.add_argument('files', metavar='FILE', nargs=1)
    openapi.add_argument(
        '--format',
        choices=('yaml', 'json'),
        default='yaml',
        help='YAML or JSON output (default: yaml)',
    )
    _add_output(openapi)
    _add_common(openapi)

    jsonschema = targets.add_parser('jsonschema', help='export an effective RAML type as JSON Schema draft-07')
    jsonschema.add_argument('files', metavar='FILE.raml', nargs=1)
    jsonschema.add_argument('name', metavar='TYPE', nargs='?')
    _add_output(jsonschema)
    _add_common(jsonschema)

    raml = targets.add_parser('raml', help='export a JSON Schema as a RAML DataType or Library')
    raml.add_argument('file', metavar='FILE.json')
    _add_output(raml)
    _add_common(raml)


def _add_lint(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lint = commands.add_parser('lint', help='check the effective document against lint rules')
    lint.add_argument('files', metavar='FILE', nargs='*')
    lint.add_argument(
        '--severity',
        choices=('error', 'warning', 'info'),
        default='info',
        help='show this severity and worse (default: info)',
    )
    lint.add_argument(
        '--fail-on',
        choices=('error', 'warning'),
        default='error',
        help='exit 1 when a finding has this severity or worse (default: error)',
    )
    lint.add_argument(
        '--rule',
        action='append',
        default=[],
        metavar='ID[=SEVERITY|off]',
        help='enable, regrade, or disable one rule; repeat for more',
    )
    lint.add_argument(
        '--ruleset',
        action='append',
        default=[],
        metavar='NAME',
        help='enable one ruleset in addition to the configured ones; repeat for more',
    )
    lint.add_argument(
        '--format',
        choices=('human', 'text', 'json', 'summary'),
        default='human',
        help='finding output format (default: human)',
    )
    lint.add_argument('--no-color', action='store_true', help='disable color in human output')
    lint.add_argument('--list-rules', action='store_true', help='list available rules and exit')
    lint.add_argument('--explain', metavar='RULE', help='explain one rule and exit')
    lint.add_argument(
        '--metrics',
        action='store_true',
        help='report what each rule and provider cost, on stderr',
    )
    lint.add_argument(
        '--max-findings',
        type=int,
        default=1000,
        metavar='N',
        help='show at most N findings across the run; 0 disables the limit (default: 1000)',
    )
    lint.add_argument(
        '--max-findings-per-rule',
        type=int,
        default=100,
        metavar='N',
        help='show at most N findings from one rule; 0 disables the limit (default: 100)',
    )
    _add_output(lint)
    _add_common(lint)


def _add_compat(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The compatibility verb. Its own function because `build_parser` is at its limit."""
    compat = commands.add_parser('compat', help='compare two versions for backward compatibility, and what breaks')
    compat.add_argument('files', metavar='FILE', nargs=2, help='the old document, then the new one')
    compat.add_argument('--json', action='store_true', help='one JSON object per change')
    compat.add_argument(
        '--types',
        action='store_true',
        help='compare `types:` declarations instead of operations, graded both ways',
    )
    compat.add_argument(
        '--breaking-only', action='store_true', help='report only breaking changes (still exits 1 if any)'
    )
    compat.add_argument(
        '--severity',
        choices=('breaking', 'review', 'compatible', 'cosmetic'),
        default='cosmetic',
        help='show this severity and worse (default: cosmetic, meaning everything)',
    )
    compat.add_argument(
        '--rule',
        action='append',
        default=[],
        metavar='ID=IMPACT|off',
        help='regrade or disable one compatibility rule; repeat for more',
    )
    _add_output(compat)
    _add_common(compat)


def _add_output(parser: argparse.ArgumentParser) -> None:
    """`-o` for every verb whose output is a document.

    A shell redirect on Windows may write CRLF, so committed output (such as
    `tree` JSON) would differ from what CI regenerates. For `compat`, which
    exits 1 on breaking changes, `-o` also separates "report not written" (an
    error message) from "report written, changes breaking".
    """
    parser.add_argument(
        '-o',
        '--output',
        metavar='FILE',
        help='write to FILE instead of stdout; UTF-8 with LF newlines',
    )


def _add_skills(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The one verb that reads no RAML, so it takes none of the common flags."""
    skills = commands.add_parser('skills', help='the agent guides this CLI ships with')
    skills.add_argument(
        'action', choices=('list', 'get', 'install'), help='list the guides, print one, or install one for an agent'
    )
    skills.add_argument('names', metavar='NAME', nargs='*', help='which guide; install defaults to the stub')
    skills.add_argument('--full', action='store_true', help="get: also print each guide's reference files")
    skills.add_argument('--json', action='store_true', help='structured output rather than Markdown')
    skills.add_argument('--user', action='store_true', help=f'install: write to ~/{SKILL_DIR} rather than the CWD')
    skills.add_argument('--dir', metavar='PATH', help='install: a skills directory of your own, overriding --user')
    skills.add_argument('--force', action='store_true', help='install: replace a skill that is already there')


def _add_serve(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`serve`: the tree projection over HTTP through the viewer bundle."""
    serve = commands.add_parser(
        'serve', help='the document in a browser, over the viewer bundle (needs fastraml-viewer)'
    )
    serve.add_argument('files', metavar='FILE', nargs=1)
    serve.add_argument('--host', default='127.0.0.1', help='interface to bind (default: 127.0.0.1, loopback only)')
    serve.add_argument('--port', type=int, default=8000, help='port to bind (default: 8000)')
    _add_common(serve)


def _add_lsp(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`lsp`: a language server on stdin and stdout. The editor names the folders."""
    lsp = commands.add_parser('lsp', help='a language server over stdio (needs pygls)')
    lsp.add_argument('--config', metavar='FILE', help='common FastRAML configuration in YAML')
    lsp.add_argument('-r', '--remote', action='store_true', help='allow http(s) includes')


def _add_navigation(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`list` (what can be named), `refs`/`deps` (what reaches a name), `show`."""
    for name, direction in (('refs', 'uses'), ('deps', 'is made of')):
        walk = commands.add_parser(name, help=f'what {direction} a named type, with the route to it')
        walk.add_argument('files', metavar='FILE', nargs=1)
        walk.add_argument('name', metavar='NAME', help='a declared name, or a whole node IRI')
        walk.add_argument('--json', action='store_true', help='one JSON object per result')
        if name == 'refs':
            walk.add_argument(
                '--sites', action='store_true', help='where the name is written, as FILE:LINE:COLUMN, instead'
            )
        walk.add_argument('--depth', type=int, default=None, metavar='N', help='stop after N hops')
        walk.add_argument(
            '--kind',
            action='append',
            metavar='KIND',
            help='keep only results of this kind, e.g. Operation; repeatable',
        )
        walk.add_argument(
            '--limit',
            type=int,
            default=_DEFAULT_LIMIT,
            metavar='N',
            help=f'print at most N results (default: {_DEFAULT_LIMIT}; 0 for all)',
        )
        _add_common(walk)

    catalogue = commands.add_parser('list', help='what is in the document, by kind and name')
    catalogue.add_argument('files', metavar='FILE', nargs=1)
    catalogue.add_argument(
        'pattern', metavar='PATTERN', nargs='?', help='keep only names containing this (case-insensitive)'
    )
    catalogue.add_argument('--json', action='store_true', help='one JSON object per entry')
    catalogue.add_argument(
        '--kind', action='append', metavar='KIND', help='keep only this kind, e.g. EndPoint; repeatable'
    )
    _add_common(catalogue)

    show = commands.add_parser('show', help='the effective view of one type or resource, as RAML')
    show.add_argument('files', metavar='FILE', nargs=1)
    show.add_argument('name', metavar='NAME', help='a declared name, or a whole node IRI')
    show.add_argument(
        '--depth',
        type=int,
        default=1,
        help='levels to expand; 1 names nested types rather than opening them (default: 1)',
    )
    _add_common(show)


def _add_join(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    join = commands.add_parser('join', help='combine API documents into one')
    join.add_argument('files', metavar='INPUT', nargs='+', help='API documents; the first is the primary input')
    join.add_argument('--title', help="the joined API's title")
    join.add_argument('--version', dest='api_version', metavar='VERSION', help="the joined API's version")
    join.add_argument('--description', help="the joined API's description; empty to omit it")
    join.add_argument(
        '--base-uri',
        action='append',
        default=[],
        metavar='INPUT=URI',
        help="replace one input's baseUri; repeatable",
    )
    _add_output(join)
    _add_common(join)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--config', metavar='FILE', help='common FastRAML configuration in YAML')
    parser.add_argument('-w', '--workspace-root', metavar='ROOT', help='confine file reads to this directory')
    parser.add_argument(
        '--no-workspace-guard',
        action='store_true',
        help='read any path the process can reach; disables the sandbox',
    )
    parser.add_argument('-r', '--remote', action='store_true', help='allow http(s) includes')
