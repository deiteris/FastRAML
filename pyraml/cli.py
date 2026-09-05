"""The `pyraml` console script — docs/13-public-api.md section 8.

```
pyraml validate [-w ROOT] [--no-workspace-guard] [-r] [-v] [--json] FILE...
pyraml info [-w ROOT] [-r] FILE
pyraml graph [--format nt|turtle|dot|json] FILE
pyraml list FILE [PATTERN]
pyraml refs FILE NAME
pyraml deps FILE NAME
pyraml query FILE (-q SPARQL | -Q FILE.rq) [--json]
```

Mirrors the reference implementation's `raml` tool closely enough that the two
can be diffed fixture by fixture (docs/14-testing.md section 1.3), which is why
`--json` emits the same trace-chain shape `RamlError.to_dict()` produces and why
`validate` keeps going after a failing file rather than stopping at it.

Everything here is presentation. No parsing rule lives in this module: it turns
arguments into a `ParseOptions`, calls an entry point, and formats what comes
back.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from pyraml import __version__

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyraml.diff import Change, Rule
    from pyraml.graph import Graph
    from pyraml.parser.entry import ParseOptions
    from pyraml.registry import Raml

__all__ = ['main']

EXIT_OK = 0
EXIT_INVALID = 1


#: One entry per subcommand. A table rather than a `match`, so adding a verb is
#: one line here and one in `_parser` rather than a branch that lint counts.
_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return _COMMANDS[args.command](args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='pyraml', description='Parse and validate RAML 1.0.')
    parser.add_argument('--version', action='version', version=f'pyraml {__version__}')
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
    _add_common(graph)

    _add_navigation(commands)

    changed = commands.add_parser('diff', help='what changed between two versions, and what it breaks')
    changed.add_argument('files', metavar='FILE', nargs=2, help='the old document, then the new one')
    changed.add_argument('--json', action='store_true', help='one JSON object per change')
    changed.add_argument(
        '--breaking-only', action='store_true', help='report only breaking changes (still exits 1 if any)'
    )
    changed.add_argument(
        '--severity',
        action='append',
        choices=('breaking', 'risky', 'safe', 'cosmetic'),
        help='report only these severities; repeatable',
    )
    _add_common(changed)

    query = commands.add_parser('query', help='run SPARQL over the graph (needs pyoxigraph)')
    query.add_argument('files', metavar='FILE', nargs='*')
    source = query.add_mutually_exclusive_group()
    source.add_argument('-q', dest='sparql', help='the query text')
    source.add_argument('-Q', dest='query_file', help='a file holding the query')
    source.add_argument('-n', dest='named', metavar='NAME', help='a query from the catalogue (docs/16 section 6)')
    query.add_argument('--list', dest='catalogue', action='store_true', help='list the catalogue and exit')
    query.add_argument('--show', metavar='NAME', help='print one catalogue query rather than running it')
    query.add_argument('--json', action='store_true', help='JSON rather than a table')
    _add_common(query)

    _COMMANDS.update(
        validate=_validate,
        info=_info,
        graph=_graph,
        refs=_walk,
        deps=_walk,
        show=_show_type,
        list=_list,
        diff=_diff,
        query=_query,
    )
    return parser


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
        walk.add_argument('--limit', type=int, default=0, metavar='N', help='print at most N results (0: all)')
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

    from pyraml.errors import RamlError  # noqa: PLC0415
    from pyraml.parser.entry import parse_from_path  # noqa: PLC0415

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
            print(f'{path}: invalid', file=sys.stderr)
            print(error, file=sys.stderr)
            sys.stderr.flush()
        elif args.verbose:
            print(f'{path}: valid ({elapsed:.1f} ms)')
        if args.verbose > 1 and raml is not None:
            _report(raml, elapsed)
    return EXIT_INVALID if failed else EXIT_OK


# -- info ---------------------------------------------------------------------


def _info(args: argparse.Namespace) -> int:
    import time  # noqa: PLC0415 - version and catalogue commands do not parse

    from pyraml.errors import RamlError  # noqa: PLC0415
    from pyraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = args.files[0]
    started = time.perf_counter()
    try:
        raml = parse_from_path(path, _options(args))
    except RamlError as err:
        print(f'{path}: invalid', file=sys.stderr)
        print(err, file=sys.stderr)
        return EXIT_INVALID
    _report(raml, (time.perf_counter() - started) * 1e3, path=path)
    return EXIT_OK


def _report(raml: Raml, elapsed: float, *, path: str | None = None) -> None:
    """The counts a user reaches for when asking why a parse was slow or wrong."""
    from pyraml.yamlnode import backend_name  # noqa: PLC0415 - reporting only

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

        print(json.dumps(graph.to_json(), indent=2))
        return EXIT_OK
    emit = {'nt': graph.to_ntriples, 'turtle': graph.to_turtle, 'dot': graph.to_dot}[args.format]
    for line in emit():
        print(line)
    return EXIT_OK


def _show_type(args: argparse.Namespace) -> int:
    """The effective view: every inherited property in one place, with origins.

    Renders from the **model**, not from the graph — the graph is only what
    turns `NAME` into one declaration. The projection drops facet detail on
    purpose, so rendering from it would show a lossy copy (docs/16 § 9).
    """
    from pyraml.render import Sources, render, render_endpoint, render_operation  # noqa: PLC0415 - graph commands only

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
        path = str(graph.nodes[iri].attributes.get('path') or '')
        lines = render_operation(operation, path, depth=depth, root=root, sources=Sources.of(raml))
    elif shape is not None:
        lines = render(shape, depth=depth, root=root)
    else:
        # A trait, a security scheme, a payload: real nodes with nothing of
        # their own to show. Naming the kind beats "not found", which is false.
        print(f'{args.name}: nothing to show for a {graph.kind_of(iri)}; try `pyraml refs`', file=sys.stderr)
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

    for kind, name, iri in entries:
        if args.json:
            import json  # noqa: PLC0415 - only JSON output needs the encoder

            print(json.dumps({'kind': kind, 'name': name, 'iri': iri, 'at': _position_of(graph, iri)}))
            continue
        print(f'{kind:<16} {_position_of(graph, iri):<22} {name}')

    if not entries:
        sys.stdout.flush()
        detail = f' matching {args.pattern!r}' if args.pattern else ''
        print(f'nothing{detail}', file=sys.stderr)
        return EXIT_INVALID
    return EXIT_OK


def _walk(args: argparse.Namespace) -> int:
    """`refs` walks the edges backwards, `deps` forwards.

    One function because they differ in exactly two values, and writing them
    twice is how the two edge closures drift apart.
    """
    from pyraml.graph import TYPE_EDGES, USE_EDGES  # noqa: PLC0415 - graph commands only

    built = _built(args)
    if built is None:
        return EXIT_INVALID
    graph, _ = built
    origin = _resolve(graph, args.name)
    if origin is None:
        return EXIT_INVALID

    reverse = args.command == 'refs'
    routes = graph.walk(origin, USE_EDGES if reverse else TYPE_EDGES, reverse=reverse, max_depth=args.depth)
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


def _diff(args: argparse.Namespace) -> int:
    """What changed, graded by whether it breaks a caller.

    Exits 1 when anything is breaking, so it works as a CI gate. `--json` is
    the whole change list with its grading, for a consumer that disagrees with
    the built-in policy and wants only the facts (docs/16 § 10).
    """
    from pyraml.diff import RULES, classify, diff  # noqa: PLC0415 - graph commands only

    graphs = []
    for path in args.files:
        built = _built(args, path)
        if built is None:
            return EXIT_INVALID
        graphs.append(built[0])

    wanted = set(args.severity or ()) | ({'breaking'} if args.breaking_only else set())
    graded = [(classify(change), change) for change in diff(graphs[0], graphs[1])]
    breaking = sum(rule.severity == 'breaking' for rule, _ in graded)
    shown = [(rule, change) for rule, change in graded if not wanted or rule.severity in wanted]

    if args.json:
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        for rule, change in shown:
            print(json.dumps(_record(rule, change)))
    else:
        # Grouped, because one edit reaches every site that used the type: the
        # declaration and each endpoint carrying it are separate nodes and so
        # separate changes. All of them are worth seeing; three copies of the
        # same sentence are not.
        groups: dict[tuple[str, str, str], list[Change]] = {}
        for rule, change in shown:
            key = (rule.name, str(change.attribute or ''), f'{_value(change.before)} -> {_value(change.after)}')
            groups.setdefault(key, []).append(change)
        for (name, attribute, values), members in groups.items():
            detail = f'  {attribute}: {values}' if attribute else ''
            print(f'{RULES[name].severity:<9} {name}{detail}')
            for change in members:
                print(f'    {_pretty(change.iri)}')
    if breaking and not args.json:
        sys.stdout.flush()
        print(f'{breaking} breaking change{"s" if breaking > 1 else ""}', file=sys.stderr)
    return EXIT_INVALID if breaking else EXIT_OK


def _plain(value: object) -> object:
    return list(value) if isinstance(value, tuple) else value


def _value(value: object) -> str:
    """One side of a change, for a person. An IRI is shown as its path.

    A reference change carries node IRIs, and printing those raw would undo the
    work `_pretty` does everywhere else in this output.
    """
    if isinstance(value, str) and value.startswith(('pyraml://', 'file://', 'http')):
        return _pretty(value)
    return repr(_plain(value))


def _record(rule: Rule, change: Change) -> dict[str, object]:
    """One change as JSON, carrying everything `classify` used to grade it.

    `directions` is a list, not one side. A type that is a POST body and a GET
    response is graded on the worse of the two, so a record naming only one of
    them contradicts its own `rule` — `direction: request` beside
    `response-property-optional` — and a consumer regrading these facts its own
    way cannot reach the same answer from them.
    """
    return {
        'kind': change.kind,
        'iri': change.iri,
        'node_kind': change.node_kind,
        'directions': sorted(change.directions),
        'attribute': change.attribute,
        'before': _plain(change.before),
        'after': _plain(change.after),
        'rule': rule.name,
        'severity': rule.severity,
        'because': rule.because,
    }


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
    from pyraml.queries import QUERIES  # noqa: PLC0415 - query command only

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
    return EXIT_INVALID if built is None else _run_sparql(built[0], text, json_lines=args.json)


def _run_sparql(graph: Graph, text: str, *, json_lines: bool) -> int:
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
    if isinstance(result, pyoxigraph.QueryBoolean):
        answer = bool(result)
        print(json.dumps({'ask': answer}) if json_lines else str(answer).lower())
        return EXIT_OK
    if isinstance(result, pyoxigraph.QueryTriples):
        for triple in result:
            print(f'{triple.subject} {triple.predicate} {triple.object} .')
        return EXIT_OK

    names = [str(name).lstrip('?') for name in result.variables]
    for row in result:
        if json_lines:
            print(json.dumps({name: _term(row[name]) for name in names}))
        else:
            print('\t'.join(_term(row[name]) or '' for name in names))
    return EXIT_OK


def _built(args: argparse.Namespace, path: str | None = None) -> tuple[Graph, Raml] | None:
    """Parse and project, or report why not.

    Returns the model as well as the graph. The graph answers "which entity did
    you mean"; several verbs then need the model to say anything detailed about
    it, and re-parsing to get it back would be absurd.

    `validate` is off here and on for `validate`/`info`: a document with a bad
    example still has a graph worth reading, and refusing to draw one would make
    the tool useless exactly where navigating is most wanted.
    """
    from pyraml.errors import RamlError  # noqa: PLC0415 - graph commands only
    from pyraml.graph import build_graph  # noqa: PLC0415
    from pyraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = path or args.files[0]
    try:
        raml = parse_from_path(path, _options(args, validate=False))
    except RamlError as err:
        print(f'{path}: invalid', file=sys.stderr)
        print(err, file=sys.stderr)
        return None
    return build_graph(raml), raml


def _position_of(graph: Graph, iri: str) -> str:
    """`path:line` for a node, so a result is somewhere you can go.

    The graph already carries this on every node it positioned; the route
    renderer used to drop it, which left `refs` telling you that something uses
    a type without telling you where to look.
    """
    node = graph.nodes.get(iri)
    if node is None:
        return ''
    where, line = node.attributes.get('definedIn'), node.attributes.get('line')
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
            print("try 'pyraml list' to see what is here", file=sys.stderr)
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

    from pyraml.queries import QUERIES, render  # noqa: PLC0415

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
    from pyraml.queries import QUERIES, render  # noqa: PLC0415 - query command only

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


# -- options ------------------------------------------------------------------


def _options(args: argparse.Namespace, *, validate: bool = True) -> ParseOptions:
    """`unwrap` is always on; `validate` is on wherever the job is to find faults."""
    from pyraml.loaders import FileLoader  # noqa: PLC0415 - parsing commands only
    from pyraml.parser.entry import ParseOptions  # noqa: PLC0415

    return ParseOptions(
        unwrap=True,
        validate=validate,
        workspace_root=args.workspace_root,
        file_loader=FileLoader() if args.no_workspace_guard else None,
        http_client=_http_client() if args.remote else None,
    )


def _http_client() -> Any:
    """A client for `-r`, from whichever of the two usual libraries is installed.

    pyRAML depends on neither — `HTTPLoader` duck-types `get(url)` — so the CLI
    is where one has to be produced, and where a user who asked for remote
    includes without a client gets told so.
    """
    for module_name, factory in (('httpx', 'Client'), ('requests', 'Session')):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        return getattr(module, factory)()
    message = '--remote needs an HTTP client: install httpx or requests'
    raise SystemExit(message)


if __name__ == '__main__':
    sys.exit(main())
