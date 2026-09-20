"""The `fastraml` console script — docs/13-public-api.md section 8.

```
fastraml validate [-w ROOT] [--no-workspace-guard] [-r] [-v] [--json] FILE...
fastraml info [-w ROOT] [-r] FILE
fastraml graph [--format nt|turtle|dot|json] [-o FILE] FILE
fastraml openapi [--format yaml|json] [-o FILE] FILE
fastraml lint [--config FILE] [--format human|text|json|summary] FILE...
fastraml serve [--host H] [--port P] FILE
fastraml list FILE [PATTERN]
fastraml refs FILE NAME
fastraml deps FILE NAME
fastraml query FILE (-q SPARQL | -Q FILE.rq) [--json] [-o FILE]
fastraml skills (list | get NAME...) [--full] [--json]
```

Mirrors go-raml's `raml` tool closely enough that the two
can be diffed fixture by fixture (docs/14-testing.md section 1.3), which is why
`--json` emits the same trace-chain shape `RamlError.to_dict()` produces and why
`validate` keeps going after a failing file rather than stopping at it.

Everything here is presentation. No parsing rule lives in this module: it turns
arguments into a `ParseOptions`, calls an entry point, and formats what comes
back.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from fastraml import __version__

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from fastraml.errors import RamlError
    from fastraml.parser.entry import ParseOptions
    from fastraml.registry import Raml
    from fastraml.views.graph import Graph
    from fastraml.views.lint import Config as LintConfig
    from fastraml.views.lint import Registry as LintRegistry

__all__ = ['main']

EXIT_OK = 0
EXIT_INVALID = 1

#: How many routes `refs`/`deps` print before saying there are more. One type at
#: a real scale produces thousands -- `refs errorScheme` on a 149-endpoint API is
#: 1902 lines -- which scrolls the answer off the screen as surely as printing
#: nothing. The remainder goes to stderr, so a piped run is unaffected, and
#: `--limit 0` still means all.
_DEFAULT_LIMIT = 50


#: One entry per subcommand. A table rather than a `match`, so adding a verb is
#: one line here and one in `_parser` rather than a branch that lint counts.
_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if hasattr(args, 'config'):
        from fastraml.config import load_config  # noqa: PLC0415 - only parsing commands carry configuration

        try:
            args.fastraml_config = load_config(args.config)
        except (OSError, TypeError, ValueError) as err:
            print(f'config: {err}', file=sys.stderr)
            return EXIT_INVALID
    return _COMMANDS[args.command](args)


def _parser() -> argparse.ArgumentParser:
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

    graph = commands.add_parser('graph', help='project the effective model as a graph (doc 16)')
    graph.add_argument('files', metavar='FILE', nargs=1)
    graph.add_argument(
        '--format',
        choices=('nt', 'turtle', 'dot', 'json'),
        default='turtle',
        help='N-Triples, Turtle, Graphviz, or plain JSON (default: turtle)',
    )
    _add_output(graph)
    _add_common(graph)

    openapi = commands.add_parser('openapi', help='export the effective API as OpenAPI 3.0.3')
    openapi.add_argument('files', metavar='FILE', nargs=1)
    openapi.add_argument(
        '--format',
        choices=('yaml', 'json'),
        default='yaml',
        help='YAML or JSON output (default: yaml)',
    )
    _add_output(openapi)
    _add_common(openapi)

    tree = commands.add_parser('tree', help='the effective document as an addressed JSON tree (doc 16 section 11)')
    tree.add_argument('files', metavar='FILE', nargs=1)
    tree.add_argument('--positions', action='store_true', help='the span of every declaration instead of the document')
    _add_output(tree)
    _add_common(tree)

    _add_serve(commands)
    _add_navigation(commands)

    _add_compat(commands)

    query = commands.add_parser('query', help='run SPARQL over the graph (needs pyoxigraph)')
    query.add_argument('files', metavar='FILE', nargs='*')
    source = query.add_mutually_exclusive_group()
    source.add_argument('-q', dest='sparql', help='the query text')
    source.add_argument('-Q', dest='query_file', help='a file holding the query')
    source.add_argument('-n', dest='named', metavar='NAME', help='a query from the catalogue (docs/16 section 6)')
    query.add_argument('--list', dest='catalogue', action='store_true', help='list the catalogue and exit')
    query.add_argument('--show', metavar='NAME', help='print one catalogue query rather than running it')
    query.add_argument('--json', action='store_true', help='JSON rather than a table')
    _add_output(query)
    _add_common(query)

    _add_lint(commands)

    _add_skills(commands)

    _COMMANDS.update(
        validate=_validate,
        info=_info,
        graph=_graph,
        openapi=_openapi,
        tree=_tree,
        serve=_serve,
        refs=_walk,
        deps=_walk,
        show=_show_type,
        list=_list,
        compat=_compat,
        query=_query,
        lint=_lint,
        skills=_skills,
    )
    return parser


def _add_lint(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lint = commands.add_parser('lint', help='check the effective document against lint rules (doc 18)')
    lint.add_argument('files', metavar='FILE', nargs='*')
    lint.add_argument(
        '--severity',
        choices=('error', 'warning', 'info'),
        default='info',
        help='show this severity and worse (default: info)',
    )
    lint.add_argument(
        '--rule',
        action='append',
        default=[],
        metavar='ID[=SEVERITY|off]',
        help='enable, regrade, or disable one rule; repeat for more',
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
        help='report what each rule and provider cost, on stderr (doc 18 section 7.1)',
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
    """The compatibility verb. Its own function because `_parser` is at its limit."""
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
    """`-o` for a verb whose output is a document, plus lint's CI report.

    On every verb that emits one, not just `openapi`. The reason the flag exists
    is that a shell redirect writes CRLF on Windows, which silently makes
    committed output differ from what CI regenerates -- and `tree` is the verb
    whose output this repository actually commits.

    `compat` needs it for a second reason: it exits 1 by design when anything is
    breaking, so `compat ... > report.md` leaves a shell with a failed command and
    no way to tell a report it wrote from one it did not.
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
    skills.add_argument('--user', action='store_true', help=f'install: write to ~/{_SKILL_DIR} rather than the CWD')
    skills.add_argument('--dir', metavar='PATH', help='install: a skills directory of your own, overriding --user')
    skills.add_argument('--force', action='store_true', help='install: replace a skill that is already there')


def _add_serve(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The one verb that runs the document rather than printing it.

    Split out of `_parser` for its statement count alone, the way
    `_add_navigation` is: `PLR0915` objects the moment the budget is spent, and
    this verb is the one that may grow, because it is the only one with a socket.
    """
    serve = commands.add_parser(
        'serve', help='the document in a browser, over the viewer bundle (needs fastraml-viewer)'
    )
    serve.add_argument('files', metavar='FILE', nargs=1)
    serve.add_argument('--host', default='127.0.0.1', help='interface to bind (default: 127.0.0.1, loopback only)')
    serve.add_argument('--port', type=int, default=8000, help='port to bind (default: 8000)')
    _add_common(serve)


def _add_navigation(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The verbs that take you around a document rather than judging it.

    Split out of `_parser` for its statement count alone, but the grouping is
    real: `list` says what can be named, `refs`/`deps` say what reaches a name,
    and `show` says what one name resolves to.
    """
    for name, direction in (('refs', 'uses'), ('deps', 'is made of')):
        walk = commands.add_parser(name, help=f'what {direction} a named type, with the route to it')
        walk.add_argument('files', metavar='FILE', nargs=1)
        walk.add_argument('name', metavar='NAME', help='a declared name, or a whole node IRI')
        walk.add_argument('--json', action='store_true', help='one JSON object per result')
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

    show = commands.add_parser('show', help='the effective view of one type, as RAML (doc 16 section 9)')
    show.add_argument('files', metavar='FILE', nargs=1)
    show.add_argument('name', metavar='NAME', help='a declared name, or a whole node IRI')
    show.add_argument(
        '--depth',
        type=int,
        default=1,
        help='levels to expand; 1 names nested types rather than opening them (default: 1)',
    )
    _add_common(show)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--config', metavar='FILE', help='common FastRAML configuration in YAML')
    parser.add_argument('-w', '--workspace-root', metavar='ROOT', help='confine file reads to this directory')
    parser.add_argument(
        '--no-workspace-guard',
        action='store_true',
        help='read any path the process can reach; disables the sandbox',
    )
    parser.add_argument('-r', '--remote', action='store_true', help='allow http(s) includes')


# -- validate -----------------------------------------------------------------


def _validate(args: argparse.Namespace) -> int:
    import time  # noqa: PLC0415 - version and catalogue commands do not parse

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415

    options = _options(args)
    failed = False
    for path in args.files:
        started = time.perf_counter()
        error: RamlError | None = None
        try:
            raml = parse_from_path(path, options)
        except RamlError as err:
            error, raml = err, None
        elapsed = (time.perf_counter() - started) * 1e3
        failed = failed or error is not None

        if args.json:
            import json  # noqa: PLC0415 - only JSON output needs the encoder

            record = {'path': path, 'valid': error is None, 'error': error.to_dict() if error else None}
            print(json.dumps(record))
            continue

        if error is not None:
            # Flushed first, or the two streams interleave by buffer rather than
            # by file once either is redirected, and a multi-file run reports
            # the failures before the successes that preceded them.
            sys.stdout.flush()
            _invalid(path, error)
            sys.stderr.flush()
        elif args.verbose:
            print(f'{path}: valid ({elapsed:.1f} ms)')
        if args.verbose > 1 and raml is not None:
            _report(raml, elapsed)
    return EXIT_INVALID if failed else EXIT_OK


# -- info ---------------------------------------------------------------------


def _info(args: argparse.Namespace) -> int:
    import time  # noqa: PLC0415 - version and catalogue commands do not parse

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = args.files[0]
    started = time.perf_counter()
    try:
        raml = parse_from_path(path, _options(args))
    except RamlError as err:
        _invalid(path, err)
        return EXIT_INVALID
    _report(raml, (time.perf_counter() - started) * 1e3, path=path)
    return EXIT_OK


# -- lint ---------------------------------------------------------------------


def _lint(args: argparse.Namespace) -> int:  # noqa: PLR0911, PLR0915 - command failures return at their source
    import yaml  # noqa: PLC0415 - lint section handoff only

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415
    from fastraml.views.lint import (  # noqa: PLC0415
        Linter,
        Severity,
        at_least,
        builtin_registry,
        discover_plugins,
        limit_findings,
        parse_config,
        parse_severity,
        render_findings,
        render_metrics,
    )

    registry = builtin_registry()
    if args.max_findings < 0 or args.max_findings_per_rule < 0:
        print('lint: finding limits must be non-negative', file=sys.stderr)
        return EXIT_INVALID
    try:
        plugins = discover_plugins(registry)
        config_text = yaml.safe_dump(dict(args.fastraml_config.lint))
        config = parse_config(config_text, registry, plugins=plugins)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as err:
        print(f'lint config: {err}', file=sys.stderr)
        return EXIT_INVALID
    try:
        config = _lint_rule_overrides(config, args.rule, registry)
    except ValueError as err:
        print(f'lint: {err}', file=sys.stderr)
        return EXIT_INVALID

    if args.list_rules:
        rows = [
            f'{rule.meta.id:<34} {rule.meta.category:<9} {rule.meta.severity:<7} {registry.source_of(rule.meta.id)}'
            for rule in registry.all()
        ]
        return _emit_document(args, '\n'.join(rows) + ('\n' if rows else ''))
    if args.explain:
        rule = registry.get(args.explain)
        if rule is None:
            print(f'{args.explain}: no such lint rule', file=sys.stderr)
            return EXIT_INVALID
        meta = rule.meta
        text = f'{meta.id} [{meta.category}, {meta.severity}]\n\n{meta.summary}\n\n{meta.rationale}\n'
        if meta.good:
            text += f'\nGood:\n\n{meta.good}'
        if meta.bad:
            text += f'\nBad:\n\n{meta.bad}'
        return _emit_document(args, text if text.endswith('\n') else text + '\n')
    if not args.files:
        print('lint: at least one FILE is required unless --list-rules or --explain is used', file=sys.stderr)
        return EXIT_INVALID

    linter = Linter(registry, config)
    findings = []
    failed = False
    for path in args.files:
        try:
            raml = parse_from_path(path, _options(args, validate=False, retain_source=True))
        except RamlError as err:
            _invalid(path, err)
            failed = True
            continue
        if not args.metrics:
            findings.extend(linter.run(raml))
            continue
        # One block per file rather than a total: "which file is slow" is the
        # question a directory run raises, and a sum cannot answer it. The
        # verbosity is opt-in, which is what `--metrics` is.
        run = linter.measure(raml)
        findings.extend(run.findings)
        # stderr, so stdout stays exactly the findings report and a `--format
        # json` run remains parseable when piped (docs/13 section 8).
        print(f'== {path}', file=sys.stderr)
        print(render_metrics(run.metrics, args.format), end='', file=sys.stderr)
    shown = [finding for finding in findings if finding.severity in at_least(parse_severity(args.severity))]
    report = limit_findings(
        shown,
        max_findings=args.max_findings or None,
        max_findings_per_rule=args.max_findings_per_rule or None,
    )
    failed = failed or any(finding.severity is Severity.ERROR for finding in findings)
    color = (
        args.format == 'human'
        and not args.no_color
        and args.output is None
        and 'NO_COLOR' not in os.environ
        and sys.stdout.isatty()
    )
    emitted = _emit_document(args, render_findings(report, args.format, color=color))
    return EXIT_INVALID if failed or emitted == EXIT_INVALID else EXIT_OK


def _lint_rule_overrides(config: LintConfig, values: Sequence[str], registry: LintRegistry) -> LintConfig:
    """Apply repeatable `--rule ID[=SEVERITY|off]` entries after file config."""
    from fastraml.views.lint import Config, RuleSetting, parse_severity  # noqa: PLC0415

    seen: set[str] = set()
    rules = list(config.rules)
    for raw in values:
        rule_id, separator, action = raw.strip().partition('=')
        rule_id, action = rule_id.strip(), action.strip().lower()
        if not rule_id or (separator and not action):
            raise ValueError(f'invalid rule override: {raw!r}')
        if rule_id in seen:
            raise ValueError(f'duplicate rule override: {rule_id}')
        seen.add(rule_id)
        if registry.get(rule_id) is None:
            raise ValueError(f'unknown rule: {rule_id}')
        plugin = registry.plugin_of(rule_id)
        if plugin and plugin not in config.plugins:
            raise ValueError(f'rule {rule_id!r} requires lint plugin {plugin!r} in the config')

        severity = None if not separator or action == 'off' else parse_severity(action)
        disabled = action == 'off'
        existing_index = next(
            (index for index, setting in enumerate(rules) if setting.id == rule_id and setting.match is None),
            None,
        )
        existing = rules[existing_index] if existing_index is not None else None
        setting = RuleSetting(
            id=rule_id,
            severity=severity if separator and action != 'off' else (existing.severity if existing else None),
            disabled=disabled,
            options=existing.options if existing else {},
        )
        if existing_index is None:
            rules.append(setting)
        else:
            rules[existing_index] = setting
    return Config(extends=config.extends, plugins=config.plugins, categories=config.categories, rules=tuple(rules))


def _report(raml: Raml, elapsed: float, *, path: str | None = None) -> None:
    """The counts a user reaches for when asking why a parse was slow or wrong."""
    from fastraml.yamlnode import backend_name  # noqa: PLC0415 - reporting only

    rows = [] if path is None else [('file', path)]
    rows += [
        # Semantic as well as diagnostic: the two YAML backends do not accept
        # quite the same documents (docs/01 deviation D9).
        ('backend', backend_name()),
        ('elapsed', f'{elapsed:.1f} ms'),
        ('fragments', str(len(raml.fragments))),
        ('types', str(sum(len(shapes) for shapes in raml.fragment_typedefs.values()))),
        ('shapes', str(len(raml.shapes))),
        ('endpoints', str(len(raml.endpoints))),
        ('annotations', str(len(raml.domain_extensions))),
    ]
    for name, value in rows:
        print(f'{name:<12} {value}')


# -- graph --------------------------------------------------------------------


def _graph(args: argparse.Namespace) -> int:
    built = _built(args)
    if built is None:
        return EXIT_INVALID
    graph, _ = built
    if args.format == 'json':
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        return _emit_document(args, json.dumps(graph.to_json(), indent=2) + '\n')
    emit = {'nt': graph.to_ntriples, 'turtle': graph.to_turtle, 'dot': graph.to_dot}[args.format]
    return _emit_document(args, ''.join(f'{line}\n' for line in emit()))


def _emit_document(args: argparse.Namespace, text: str) -> int:
    """Write an export to FILE with `-o`, or to stdout without it.

    The file is opened UTF-8 with LF newlines regardless of platform, so the
    result does not depend on the shell that ran the command.
    """
    if args.output is None:
        print(text, end='')
        return EXIT_OK
    try:
        with open(args.output, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
    except OSError as err:
        print(f'{args.output}: {err}', file=sys.stderr)
        return EXIT_INVALID
    return EXIT_OK


def _openapi(args: argparse.Namespace) -> int:
    """Write the effective API as an OpenAPI 3.0 document."""
    raml = _parsed(args)
    if raml is None:
        return EXIT_INVALID

    from fastraml.views.openapi import to_openapi  # noqa: PLC0415

    document, dropped = to_openapi(raml)
    payload = document.to_dict()
    if args.format == 'json':
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        text = json.dumps(payload, indent=2) + '\n'
    else:
        import yaml  # noqa: PLC0415 - only YAML output needs the encoder

        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        if not text.endswith('\n'):
            text += '\n'
    code = _emit_document(args, text)
    for message in dropped:
        print(f'warning: {message}', file=sys.stderr)
    return code


def _tree(args: argparse.Namespace) -> int:
    """The whole effective document, with every reference as an address.

    The counterpart to `graph`: the same walk assigns both, so an address
    printed here names the node `graph` prints (docs/16 § 11). Unwrapped, so
    what is printed is the effective document rather than the declared one.
    """
    import json  # noqa: PLC0415 - only this verb needs the encoder

    from fastraml.views.tree import build_tree, positions_of  # noqa: PLC0415

    # No graph: this verb needs none, and `build_tree` assigns the addresses it
    # needs itself. A consumer that already holds a graph passes
    # `addresses=graph.addresses` instead and skips the second assignment.
    raml = _parsed(args)
    if raml is None:
        return EXIT_INVALID
    payload = positions_of(raml) if args.positions else build_tree(raml)
    # Not `sort_keys`. Declaration order is an invariant everywhere the model is
    # exposed (docs/02 § 4, docs/16 § 3.4), and sorting threw it away at the last
    # step: properties, named examples, response codes and the keys of an
    # example's own data all came out alphabetical, so a reader was shown an
    # order no author wrote. Stable output is what the *sink* wanted; the
    # golden suite sorts for itself.
    return _emit_document(args, json.dumps(payload, indent=2) + '\n')


def _serve(args: argparse.Namespace) -> int:
    """The document in a browser: the `tree` projection, served as `api.json`.

    The parse is the one `tree` uses — `validate` off, so a document with a bad
    example still has a reading worth serving — and the only new work is handing
    the projection to the `fastraml-viewer` bundle. The bundle reads `api.json`
    beside itself and ships one (the worked sample), so `fastraml_viewer.serve`
    routes that name to this document in front of the static files; without the
    shadow it would answer with the sample instead, which is a convincing wrong
    answer rather than a visible failure.

    `fastraml_viewer` is imported inside the verb, the way `query` imports
    `pyoxigraph`: a user who never serves a document never installs it.
    """
    raml = _parsed(args)
    if raml is None:
        return EXIT_INVALID

    from fastraml.views.tree import build_tree  # noqa: PLC0415 - serve verb only

    try:
        from fastraml_viewer import serve as serve_viewer  # noqa: PLC0415 - optional: fastraml[serve]
    except ImportError:
        print('serve needs the viewer: install fastraml-viewer (fastraml[serve])', file=sys.stderr)
        return EXIT_INVALID

    try:
        server = serve_viewer(build_tree(raml), host=args.host, port=args.port)
    except OSError as err:
        print(f'serve: {err}', file=sys.stderr)
        return EXIT_INVALID
    print(f'viewer: http://{args.host}:{server.server_address[1]}/  (Ctrl-C to stop)', file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def _show_type(args: argparse.Namespace) -> int:
    """The effective view: every inherited property in one place, with origins.

    Renders from the **model**, not from the graph — the graph is only what
    turns `NAME` into one declaration. The projection drops facet detail on
    purpose, so rendering from it would show a lossy copy (docs/16 § 9).
    """
    from fastraml.views.render import (  # noqa: PLC0415 - graph commands only
        Sources,
        render,
        render_endpoint,
        render_operation,
    )

    built = _built(args)
    if built is None:
        return EXIT_INVALID
    graph, raml = built
    iri = _resolve(graph, args.name)
    if iri is None:
        return EXIT_INVALID

    depth, root = max(1, args.depth), graph.root
    endpoint, operation, shape = graph.endpoint_at(iri), graph.operation_at(iri), graph.shape_at(iri)
    if endpoint is not None:
        lines = render_endpoint(endpoint, depth=depth, root=root, sources=Sources.of(raml))
    elif operation is not None:
        lines = render_operation(operation, _owning_path(graph, iri), depth=depth, root=root, sources=Sources.of(raml))
    elif shape is not None:
        lines = render(shape, depth=depth, root=root)
    else:
        # A trait, a resource type, a payload: a real node whose content is a
        # template applied elsewhere rather than an effective form of its own.
        # What *is* effective about it is where it landed, so say that and where
        # it was written rather than only refusing.
        kind, where = graph.kind_of(iri), _position_of(graph, iri)
        applied = len(graph.into(iri, ('appliesTrait', 'appliesResourceType', 'securedBy', 'annotation')))
        print(f'{args.name}: a {kind}{" at " + where if where else ""} has no effective view', file=sys.stderr)
        if applied:
            print(f'applied at {applied} site(s); see `fastraml refs {args.name}`', file=sys.stderr)
        return EXIT_INVALID
    for line in lines:
        print(line)
    return EXIT_OK


def _list(args: argparse.Namespace) -> int:
    """The inventory: what this document holds that can be named.

    `refs`, `deps` and `show` all take a NAME, and until this verb existed the
    only ways to learn one were `graph --format json` piped through a filter,
    a SPARQL query needing an optional dependency, or guessing. Counting is what
    `info` does; this names them.
    """
    built = _built(args)
    if built is None:
        return EXIT_INVALID
    graph, _ = built

    entries = graph.entries(args.kind)
    if args.pattern:
        wanted = args.pattern.casefold()
        entries = [entry for entry in entries if wanted in entry[1].casefold()]

    if not entries:
        # No `flush` here, unlike `_walk`: nothing has been written to stdout on
        # this path, so there is nothing for the note to arrive ahead of.
        detail = f' matching {args.pattern!r}' if args.pattern else ''
        print(f'nothing{detail}', file=sys.stderr)
        return EXIT_INVALID

    if args.json:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        for kind, name, iri in entries:
            print(json.dumps({'kind': kind, 'name': name, 'iri': iri, 'at': _position_of(graph, iri)}))
        return EXIT_OK

    for kind, name, iri in entries:
        print(f'{kind:<16} {_position_of(graph, iri):<22} {name}')
    return EXIT_OK


def _walk(args: argparse.Namespace) -> int:
    """`refs` walks the edges backwards, `deps` forwards.

    One function because they differ in exactly two values, and writing them
    twice is how the two edge closures drift apart.
    """
    from fastraml.views.graph import TYPE_EDGES, USE_EDGES  # noqa: PLC0415 - graph commands only

    built = _built(args)
    if built is None:
        return EXIT_INVALID
    graph, _ = built
    origin = _resolve(graph, args.name)
    if origin is None:
        return EXIT_INVALID

    reverse = args.command == 'refs'
    # `deps` follows type structure, which is the whole answer for a type and
    # none of it for a resource: an endpoint's own edges are `supportedOperation`,
    # `parameter` and `securedBy`, so the type closure alone reported that every
    # endpoint and every operation in the document is made of nothing. Forward
    # from anything that is not a type, the *use* closure is the containment.
    forward = TYPE_EDGES if graph.kind_of(origin) == 'Type' else USE_EDGES
    routes = graph.walk(origin, USE_EDGES if reverse else forward, reverse=reverse, max_depth=args.depth)
    if args.kind:
        wanted = {kind.casefold() for kind in args.kind}
        routes = [route for route in routes if graph.kind_of(route.target).casefold() in wanted]
    paths = routes[: args.limit] if args.limit else routes

    for path in paths:
        # Rendered from whichever end is the *subject* of the first hop, so a
        # route reads the way the edges point no matter which way it was walked.
        nodes = tuple(reversed(path.nodes)) if reverse else path.nodes
        predicates = tuple(reversed(path.predicates)) if reverse else path.predicates
        where = _position_of(graph, path.target)
        if args.json:
            import json  # noqa: PLC0415 - only JSON output needs the encoder

            record = {'kind': graph.kind_of(path.target), 'iri': path.target, 'at': where, 'route': list(nodes)}
            print(json.dumps(record))
            continue
        route = graph.label(nodes[0])
        for predicate, node in zip(predicates, nodes[1:], strict=True):
            route += f' -{predicate}-> {graph.label(node)}'
        # Kind and position first: what was found and where to go. The route is
        # why it was found, and is the part that varies in length.
        print(f'{graph.kind_of(path.target):<16} {where:<22} {route}')
    if len(paths) < len(routes):
        # Flushed first, or the note arrives before the results it is about
        # once either stream is redirected — the hazard `_validate` documents.
        sys.stdout.flush()
        print(f'... {len(routes) - len(paths)} more; raise --limit or narrow with --kind', file=sys.stderr)
    if not paths and not args.json:
        print(f'{args.name}: nothing found', file=sys.stderr)
    return EXIT_OK


def _compat(args: argparse.Namespace) -> int:
    """What changed, graded by whether it breaks a caller.

    Exits 1 when anything is breaking, so it works as a CI gate. `--json` is
    the whole change list with its grading, for a consumer that disagrees with
    the built-in policy and wants only the facts (docs/16 § 10).
    """
    from fastraml.views.backward import (  # noqa: PLC0415 - compat verb only
        IMPACTS,
        backward,
        backward_types,
        configure,
        record,
        render_markdown,
    )

    models = []
    for path in args.files:
        parsed = _parsed(args, path)
        if parsed is None:
            return EXIT_INVALID
        models.append(parsed)

    threshold = IMPACTS.rank('breaking' if args.breaking_only else args.severity)
    try:
        compatibility = _compatibility_rule_overrides(args.fastraml_config.compatibility, args.rule)
        compare = backward_types if args.types else backward
        changes = configure(compare(models[0], models[1]), compatibility)
    except ValueError as err:
        print(f'compat: {err}', file=sys.stderr)
        return EXIT_INVALID
    breaking = sum(change.impact == 'breaking' for change in changes)
    shown = [change for change in changes if IMPACTS.rank(change.impact) <= threshold]

    if args.json:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        text = ''.join(json.dumps(record(change)) + '\n' for change in shown)
    else:
        text = render_markdown(shown) if shown else ''

    # `-o` before the verdict: a report nobody could write is a failure of the
    # command, and saying "22 breaking changes" over it would bury that.
    written = _emit_document(args, text)
    if written != EXIT_OK:
        return written
    if breaking and not args.json:
        sys.stdout.flush()
        print(f'{breaking} breaking change{"s" if breaking > 1 else ""}', file=sys.stderr)
    return EXIT_INVALID if breaking else EXIT_OK


def _compatibility_rule_overrides(config: Any, values: Sequence[str]) -> Any:
    from typing import cast  # noqa: PLC0415 - compatibility CLI only

    from fastraml.config import CompatibilityConfig, CompatibilityRuleSetting  # noqa: PLC0415 - compat only

    rules = list(config.rules)
    for raw in values:
        rule_id, separator, action = raw.strip().partition('=')
        action = action.strip().lower()
        if not rule_id or not separator or action not in ('breaking', 'review', 'compatible', 'cosmetic', 'off'):
            raise ValueError(f'invalid compatibility rule override: {raw!r}')
        rules.append(
            CompatibilityRuleSetting(
                id=rule_id.strip(),
                impact=None if action == 'off' else cast('Any', action),
                disabled=action == 'off',
            )
        )
    return CompatibilityConfig(rules=tuple(rules))


#: IRI segments that introduce something, and how to show it. The IRI is
#: structural (docs/16 § 3) precisely so a reader-facing path can be recovered
#: from it without consulting the model again.
_SEGMENTS = {
    'endpoint': '{}',
    'supportedOperation': '{}',
    'returns': '-> {}',
    'payload': '{}',
    'property': '.{}',
    'patternProperty': '.{}',
    'parameter': '?{}',
    'anyOf': '|{}',
    'types': 'types/{}',
    'traits': 'trait {}',
    'resourceTypes': 'resourceType {}',
    'securitySchemes': 'scheme {}',
    'annotations': 'annotation {}',
}


def _pretty(iri: str) -> str:
    """One node IRI as something a person can find in the document."""
    from urllib.parse import unquote  # noqa: PLC0415 - presentation only

    parts = [unquote(part) for part in (iri.partition('#/')[2] or iri).split('/')]
    out, index = [], 0
    while index < len(parts):
        head = parts[index]
        template = _SEGMENTS.get(head)
        if template and index + 1 < len(parts):
            out.append(template.format(parts[index + 1]))
            index += 2
        elif head in ('items', 'schema', 'web-api', 'declarations', 'request'):
            index += 1
        else:
            out.append(head)
            index += 1
    return ' '.join(out) or iri


def _query(args: argparse.Namespace) -> int:
    """SPARQL over the graph: the catalogue, or a query of your own."""
    from fastraml.views.queries import QUERIES  # noqa: PLC0415 - query command only

    # The catalogue is text, so `--list` and `--show` want neither a store nor a
    # file. A user without pyoxigraph can still read a query and copy it out.
    if args.catalogue:
        width = max(len(name) for name in QUERIES)
        for query in QUERIES.values():
            print(f'{query.name:<{width}}  {query.question}')
        return EXIT_OK
    if args.show is not None:
        return _show(args.show)

    text = _query_text(args)
    if text is None:
        return EXIT_INVALID
    if not args.files:
        print('query needs a FILE', file=sys.stderr)
        return EXIT_INVALID
    built = _built(args)
    return EXIT_INVALID if built is None else _run_sparql(args, built[0], text)


def _run_sparql(args: argparse.Namespace, graph: Graph, text: str) -> int:
    """Load the graph into a store and print whatever the query returns.

    `pyoxigraph` is optional the way `google-re2` and the HTTP client are: the
    package never imports it at module scope, so a user who does not query never
    installs it. It is used inline rather than behind a helper because the three
    result classes are what the dispatch needs, and they are only in scope once
    the import has succeeded.
    """
    import io  # noqa: PLC0415 - query execution only
    import json  # noqa: PLC0415 - query execution only

    try:
        import pyoxigraph  # noqa: PLC0415 - optional: a module-level import would make it required
    except ImportError:
        print('query needs an RDF store: install pyoxigraph', file=sys.stderr)
        return EXIT_INVALID

    store = pyoxigraph.Store()
    store.load(io.StringIO('\n'.join(graph.to_ntriples())), format=pyoxigraph.RdfFormat.N_TRIPLES)
    result = store.query(text)

    # SPARQL has three result shapes and the store returns a different class for
    # each: `QuerySolutions` for SELECT, `QueryTriples` for CONSTRUCT/DESCRIBE,
    # `QueryBoolean` for ASK. Handling only the first turns a valid query into a
    # traceback. The ASK result is *not* a `bool` — it is a wrapper that converts
    # to one — which is why this dispatches on the class rather than on
    # `isinstance` of `bool`.
    json_lines = args.json
    if isinstance(result, pyoxigraph.QueryBoolean):
        answer = bool(result)
        rows = [json.dumps({'ask': answer}) if json_lines else str(answer).lower()]
    elif isinstance(result, pyoxigraph.QueryTriples):
        rows = [f'{triple.subject} {triple.predicate} {triple.object} .' for triple in result]
    else:
        names = [str(name).lstrip('?') for name in result.variables]
        rows = [
            json.dumps({name: _term(row[name]) for name in names})
            if json_lines
            else '\t'.join(_term(row[name]) or '' for name in names)
            for row in result
        ]
    return _emit_document(args, ''.join(f'{row}\n' for row in rows))


def _invalid(path: str, error: RamlError) -> None:
    """Report a parse failure, and what to do about it where that is knowable."""
    print(f'{path}: invalid', file=sys.stderr)
    print(error, file=sys.stderr)
    hint = _workspace_hint(error)
    if hint:
        print(hint, file=sys.stderr)


def _workspace_hint(error: RamlError) -> str:
    """The `-w` a refused read would have needed, if widening would have helped.

    The root defaults to the *entry file's directory*, so an API whose libraries
    sit beside it rather than beneath it fails on its first `!include` -- and the
    refusal cannot say which flag widens the root, because that is this layer's
    vocabulary and not `loaders.py`'s. It computes the value; this names the flag.
    """
    for chain in error.chains():
        for frame in chain:
            suggested = frame.info.get('suggested_root') if frame.info else None
            if suggested:
                return (
                    f"hint: the workspace root defaults to the entry file's directory; pass -w {suggested} to widen it"
                )
    return ''


def _parsed(args: argparse.Namespace, path: str | None = None) -> Raml | None:
    """Parse without projecting, for a verb that needs no graph."""
    from fastraml.errors import RamlError  # noqa: PLC0415 - graph commands only
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = path or args.files[0]
    try:
        return parse_from_path(path, _options(args, validate=False))
    except RamlError as err:
        _invalid(path, err)
        return None


def _built(args: argparse.Namespace, path: str | None = None) -> tuple[Graph, Raml] | None:
    """Parse and project, or report why not.

    Returns the model as well as the graph. The graph answers "which entity did
    you mean"; several verbs then need the model to say anything detailed about
    it, and re-parsing to get it back would be absurd.

    `validate` is off here and on for `validate`/`info`: a document with a bad
    example still has a graph worth reading, and refusing to draw one would make
    the tool useless exactly where navigating is most wanted.
    """
    from fastraml.errors import RamlError  # noqa: PLC0415 - graph commands only
    from fastraml.parser.entry import parse_from_path  # noqa: PLC0415
    from fastraml.views.graph import build_graph  # noqa: PLC0415

    path = path or args.files[0]
    try:
        raml = parse_from_path(path, _options(args, validate=False))
    except RamlError as err:
        _invalid(path, err)
        return None
    return build_graph(raml), raml


def _owning_path(graph: Graph, iri: str) -> str:
    """The resource an operation hangs off, as its URI.

    Read back over the `supportedOperation` edge rather than off the operation
    node, which carries no `path` of its own — reading one there produced an
    empty key, so `show <operation>` emitted a bare `:` and the output stopped
    being loadable YAML, which is the one thing docs/16 § 9.2 promises.
    """
    for edge in graph.into(iri, ('supportedOperation',)):
        return graph.label(edge.subject)
    return ''


def _position_of(graph: Graph, iri: str) -> str:
    """`path:line` for a node, so a result is somewhere you can go.

    The graph already carries this on every node it positioned; the route
    renderer used to drop it, which left `refs` telling you that something uses
    a type without telling you where to look.
    """
    node = graph.nodes.get(iri)
    if node is None:
        return ''
    attributes = node.attributes
    where, line = attributes.get('definedIn'), attributes.get('line')
    return f'{where}:{line}' if where and line else str(where or '')


def _resolve(graph: Graph, name: str) -> str | None:
    """A name from the command line as one node IRI."""
    found = graph.find(name)
    if not found:
        print(f'{name}: no such node', file=sys.stderr)
        # A miss is still a miss — the nearest name is not run, because that
        # answers a question the caller did not ask, exactly as the ambiguity
        # branch below refuses to pick. But a dead end helps nobody.
        near = graph.suggest(name)
        if near:
            print(f'did you mean: {", ".join(near)}?', file=sys.stderr)
        else:
            print("try 'fastraml list' to see what is here", file=sys.stderr)
        return None
    if len(found) > 1:
        # Two libraries may declare the same name, and picking one silently
        # would answer a question the user did not ask.
        print(f'{name}: ambiguous, name one of:', file=sys.stderr)
        for iri in found:
            print(f'  {iri}', file=sys.stderr)
        return None
    return found[0]


def _query_text(args: argparse.Namespace) -> str | None:
    """The SPARQL to run: given, read from a file, or named in the catalogue."""
    from pathlib import Path  # noqa: PLC0415 - query files only

    from fastraml.views.queries import QUERIES, render  # noqa: PLC0415

    if args.sparql is not None:
        return str(args.sparql)
    if args.query_file is not None:
        return Path(args.query_file).read_text(encoding='utf-8')
    if args.named is not None:
        query = QUERIES.get(args.named)
        if query is None:
            print(f'{args.named}: no such query; try --list', file=sys.stderr)
            return None
        return render(query)
    print('query needs one of -q, -Q or -n (or --list)', file=sys.stderr)
    return None


def _show(name: str) -> int:
    from fastraml.views.queries import QUERIES, render  # noqa: PLC0415 - query command only

    query = QUERIES.get(name)
    if query is None:
        print(f'{name}: no such query; try --list', file=sys.stderr)
        return EXIT_INVALID
    print(f'# {query.question}')
    print(render(query), end='')
    return EXIT_OK


def _term(term: Any) -> str | None:
    """One SPARQL solution binding as text.

    `Any` because `pyoxigraph` is not a declared dependency, so its types are
    genuinely unavailable to the checker. `.value` is not a guess: every term
    class it can return — `NamedNode`, `Literal`, `BlankNode` — has one. `None`
    is the unbound case an `OPTIONAL` produces.
    """
    return None if term is None else str(term.value)


# -- skills -------------------------------------------------------------------

#: The guides this CLI serves, as a directory of Markdown inside the package.
#: An agent skill installed elsewhere is a *copy*, and a copy goes stale against
#: the version that actually answers. Serving the text from here means
#: `fastraml skills get core` always describes this build, so the installed skill
#: can be a stub that fetches rather than a duplicate that rots
#: (docs/13-public-api.md section 8.3).
_SKILLDATA: Final = 'skilldata'

#: How much of a guide's description `skills list` shows before it truncates.
#: Long enough to route on, short enough that the listing stays a listing.
_DESCRIPTION_WIDTH: Final = 96


#: The guide `skills install` writes when given no name: the discovery stub whose
#: whole body points back at `skills get`. It is `hidden:`, so it is the one
#: guide `list` does not advertise -- a listing of documentation should not
#: recommend the shim that fetches it.
_STUB: Final = 'fastraml'

#: Where an installed skill goes. `.agents/skills` rather than a client's own
#: directory: the Agent Skills specification names it the cross-client path, and
#: Claude Code, GitHub Copilot and VS Code all scan it, so one copy serves every
#: agent instead of one copy per agent. `--dir` covers anything else
#: (docs/13-public-api.md section 8.3).
_SKILL_DIR: Final = '.agents/skills'


class _Guide(NamedTuple):
    """One served guide: what `list` needs, plus where to read the rest."""

    name: str
    description: str
    path: Path
    hidden: bool


def _skills(args: argparse.Namespace) -> int:
    guides = _guides()
    if not guides:
        # Reachable only from a broken install -- the data ships in the wheel.
        print(f'no guides found in {_skill_root()}', file=sys.stderr)
        return EXIT_INVALID
    if args.action == 'list':
        return _skills_list(guides, json_mode=args.json)
    if args.action == 'install':
        return _skills_install(guides, args.names or [_STUB], args)
    return _skills_get(guides, args.names, full=args.full, json_mode=args.json)


def _skills_install(guides: dict[str, _Guide], names: Sequence[str], args: argparse.Namespace) -> int:
    """Write a guide into a skills directory, where an agent will discover it.

    Built in rather than delegated to `gh skill`, which is a separate tool, in
    preview, and not guaranteed to be present. The install is a file copy into a
    documented directory; needing a second CLI for that would be the only hard
    dependency this package has.

    Refuses to overwrite without `--force`. An installed skill is a file the user
    may have edited, and silently replacing it is the one thing an installer must
    not do.
    """
    missing = [name for name in names if name not in guides]
    if missing:
        print(f'no such guide: {", ".join(missing)}; try {", ".join(guides)}', file=sys.stderr)
        return EXIT_INVALID

    root = _install_root(args)
    written = []
    for name in names:
        destination = root / name / 'SKILL.md'
        if destination.exists() and not args.force:
            print(f'{destination} exists; pass --force to replace it', file=sys.stderr)
            return EXIT_INVALID
        written.append((destination, guides[name].path.read_text(encoding='utf-8')))

    # Every read and every collision check first: a half-finished install across
    # several names leaves the user to work out which ones landed.
    for destination, text in written:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding='utf-8')
        print(f'installed {destination}')
    return EXIT_OK


def _install_root(args: argparse.Namespace) -> Path:
    """Which skills directory to write into.

    Project scope by default, matching what the ecosystem's installers do: the
    skill then travels with the repository it was installed for, and can be
    committed beside it.
    """
    from pathlib import Path  # noqa: PLC0415 - this verb only

    if args.dir:
        return Path(args.dir).expanduser()
    return (Path.home() if args.user else Path.cwd()) / _SKILL_DIR


def _skill_root() -> Path:
    from pathlib import Path  # noqa: PLC0415 - this verb only

    return Path(__file__).parent / _SKILLDATA


def _guides() -> dict[str, _Guide]:
    """Every guide in the package, in name order."""
    root = _skill_root()
    if not root.is_dir():
        return {}
    found = {}
    for path in sorted(root.iterdir()):
        skill = path / 'SKILL.md'
        if not skill.is_file():
            continue
        front = _frontmatter(skill.read_text(encoding='utf-8'))
        name = str(front.get('name') or path.name)
        # Hidden by *name*, not by a frontmatter flag. The stub is the file that
        # gets installed, and `hidden` there is a word another client may act on
        # -- agent-browser uses it to mean "keep this out of the agent's view",
        # which is the one thing the stub must never be. Marking it in code
        # instead keeps the installed copy byte-identical to `skills/fastraml/`.
        found[name] = _Guide(name, str(front.get('description') or ''), skill, name == _STUB)
    return found


def _frontmatter(text: str) -> dict[str, Any]:
    """The YAML block a SKILL.md opens with, or an empty mapping.

    Tolerant on purpose: a guide whose frontmatter will not parse is still worth
    printing, so it falls back to the directory name rather than failing the
    verb.
    """
    import yaml  # noqa: PLC0415 - this verb only

    if not text.startswith('---\n'):
        return {}
    block, separator, _ = text[4:].partition('\n---')
    if not separator:
        return {}
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _skills_list(guides: dict[str, _Guide], *, json_mode: bool) -> int:
    """The guides worth reading. The stub is `hidden:`, so it is not one of them.

    It stays `get`-able and `install`-able by name. Hiding it keeps a listing of
    *documentation* from advertising the discovery shim whose only job is to
    fetch that documentation.
    """
    listed = [guide for guide in guides.values() if not guide.hidden]
    if json_mode:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        for guide in listed:
            print(json.dumps({'name': guide.name, 'description': guide.description}))
        return EXIT_OK

    width = max(len(guide.name) for guide in listed)
    for guide in listed:
        summary = guide.description
        if len(summary) > _DESCRIPTION_WIDTH:
            # ASCII, not an ellipsis character: this lands on a Windows console
            # under cp1252 as often as on a UTF-8 one, and `...` survives both.
            summary = summary[: _DESCRIPTION_WIDTH - 3].rstrip() + '...'
        print(f'{guide.name:<{width}}  {summary}')
    # Flushed first, or the hint arrives ahead of the listing it is about once
    # either stream is redirected -- the hazard `_validate` documents.
    sys.stdout.flush()
    print("\nRead one with 'fastraml skills get <name>'.", file=sys.stderr)
    return EXIT_OK


def _skills_get(guides: dict[str, _Guide], names: Sequence[str], *, full: bool, json_mode: bool) -> int:
    if not names:
        print(f'skills get needs a name: {", ".join(guides)}', file=sys.stderr)
        return EXIT_INVALID
    missing = [name for name in names if name not in guides]
    if missing:
        # Named, not guessed, for the same reason `_resolve` refuses to pick:
        # printing the wrong guide answers a question nobody asked.
        print(f'no such guide: {", ".join(missing)}; try {", ".join(guides)}', file=sys.stderr)
        return EXIT_INVALID

    wanted = [guides[name] for name in names]
    if json_mode:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        for guide in wanted:
            record: dict[str, object] = {'name': guide.name, 'content': guide.path.read_text(encoding='utf-8')}
            if full:
                record['references'] = [{'path': name, 'content': text} for name, text in _guide_references(guide)]
            print(json.dumps(record))
        return EXIT_OK

    for index, guide in enumerate(wanted):
        if index:
            print('\n---\n')
        print(guide.path.read_text(encoding='utf-8').rstrip())
        for name, text in _guide_references(guide) if full else ():
            print(f'\n--- {name} ---\n')
            print(text.rstrip())
    return EXIT_OK


def _guide_references(guide: _Guide) -> list[tuple[str, str]]:
    """A guide's `references/` files, in name order. Absent is not an error."""
    folder = guide.path.parent / 'references'
    if not folder.is_dir():
        return []
    return [
        (f'references/{path.name}', path.read_text(encoding='utf-8'))
        for path in sorted(folder.iterdir())
        if path.suffix == '.md'
    ]


# -- options ------------------------------------------------------------------


def _options(args: argparse.Namespace, *, validate: bool = True, retain_source: bool = False) -> ParseOptions:
    """`unwrap` is always on; `validate` is on wherever the job is to find faults."""
    from fastraml.loaders import FileLoader  # noqa: PLC0415 - parsing commands only
    from fastraml.parser.entry import ParseOptions  # noqa: PLC0415

    configured = args.fastraml_config.parser
    return ParseOptions(
        unwrap=True,
        validate=validate,
        retain_source=retain_source,
        workspace_root=args.workspace_root or configured.workspace_root,
        max_include_size=configured.max_include_size,
        file_loader=FileLoader() if args.no_workspace_guard else None,
        http_client=_http_client() if args.remote or configured.remote else None,
        regex_engine=configured.regex_engine,
        max_depth=configured.max_depth,
    )


def _http_client() -> Any:
    """A client for `-r`, from whichever of the two usual libraries is installed.

    fastRAML depends on neither — `HTTPLoader` duck-types `get(url)` — so the CLI
    is where one has to be produced, and where a user who asked for remote
    includes without a client gets told so.
    """
    for module_name, factory in (('httpx', 'Client'), ('requests', 'Session')):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        return getattr(module, factory)()
    message = '--remote needs an HTTP client: pip install "fastraml[http]" (or any httpx / requests already present)'
    raise SystemExit(message)


if __name__ == '__main__':
    sys.exit(main())
