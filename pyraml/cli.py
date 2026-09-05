"""The `pyraml` console script — docs/13-public-api.md section 8.

```
pyraml validate [-w ROOT] [--no-workspace-guard] [-r] [-v] [--json] FILE...
pyraml info [-w ROOT] [-r] FILE
pyraml graph [--format nt|turtle|dot|json] FILE
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
    from collections.abc import Sequence

    from pyraml.graph import Graph
    from pyraml.parser.entry import ParseOptions
    from pyraml.registry import Raml

__all__ = ['main']

EXIT_OK = 0
EXIT_INVALID = 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    match args.command:
        case 'info':
            return _info(args)
        case 'graph':
            return _graph(args)
        case 'refs' | 'deps':
            return _walk(args)
        case 'query':
            return _query(args)
        case _:
            return _validate(args)


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

    for name, direction in (('refs', 'uses'), ('deps', 'is made of')):
        walk = commands.add_parser(name, help=f'what {direction} a named type, with the route to it')
        walk.add_argument('files', metavar='FILE', nargs=1)
        walk.add_argument('name', metavar='NAME', help='a declared name, or a whole node IRI')
        walk.add_argument('--json', action='store_true', help='one JSON object per result')
        _add_common(walk)

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
    return parser


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
    graph = _built(args)
    if graph is None:
        return EXIT_INVALID
    if args.format == 'json':
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        print(json.dumps(graph.to_json(), indent=2))
        return EXIT_OK
    emit = {'nt': graph.to_ntriples, 'turtle': graph.to_turtle, 'dot': graph.to_dot}[args.format]
    for line in emit():
        print(line)
    return EXIT_OK


def _walk(args: argparse.Namespace) -> int:
    """`refs` walks the edges backwards, `deps` forwards.

    One function because they differ in exactly two values, and writing them
    twice is how the two edge closures drift apart.
    """
    from pyraml.graph import TYPE_EDGES, USE_EDGES  # noqa: PLC0415 - graph commands only

    graph = _built(args)
    if graph is None:
        return EXIT_INVALID
    origin = _resolve(graph, args.name)
    if origin is None:
        return EXIT_INVALID

    reverse = args.command == 'refs'
    paths = graph.walk(origin, USE_EDGES if reverse else TYPE_EDGES, reverse=reverse)
    for path in paths:
        # Rendered from whichever end is the *subject* of the first hop, so a
        # route reads the way the edges point no matter which way it was walked.
        nodes = tuple(reversed(path.nodes)) if reverse else path.nodes
        predicates = tuple(reversed(path.predicates)) if reverse else path.predicates
        if args.json:
            import json  # noqa: PLC0415 - only JSON output needs the encoder

            print(json.dumps({'kind': graph.kind_of(path.target), 'iri': path.target, 'route': list(nodes)}))
            continue
        route = graph.label(nodes[0])
        for predicate, node in zip(predicates, nodes[1:], strict=True):
            route += f' -{predicate}-> {graph.label(node)}'
        print(f'{graph.kind_of(path.target):<16} {route}')
    if not paths and not args.json:
        print(f'{args.name}: nothing found', file=sys.stderr)
    return EXIT_OK


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
    graph = _built(args)
    return EXIT_INVALID if graph is None else _run_sparql(graph, text, json_lines=args.json)


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


def _built(args: argparse.Namespace) -> Graph | None:
    """Parse and project, or report why not.

    `validate` is off here and on for `validate`/`info`: a document with a bad
    example still has a graph worth reading, and refusing to draw one would make
    the tool useless exactly where navigating is most wanted.
    """
    from pyraml.errors import RamlError  # noqa: PLC0415 - graph commands only
    from pyraml.graph import build_graph  # noqa: PLC0415
    from pyraml.parser.entry import parse_from_path  # noqa: PLC0415

    path = args.files[0]
    try:
        raml = parse_from_path(path, _options(args, validate=False))
    except RamlError as err:
        print(f'{path}: invalid', file=sys.stderr)
        print(err, file=sys.stderr)
        return None
    return build_graph(raml)


def _resolve(graph: Graph, name: str) -> str | None:
    """A name from the command line as one node IRI."""
    found = graph.find(name)
    if not found:
        print(f'{name}: no such node', file=sys.stderr)
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
