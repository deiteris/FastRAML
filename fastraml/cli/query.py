"""`query`: SPARQL over the graph. `pyoxigraph` is imported inside the verb."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, build_or_report, emit_document, fail

if TYPE_CHECKING:
    import argparse

    from fastraml.views.graph import Graph
    from fastraml.views.queries import Query


def _query(args: argparse.Namespace) -> int:
    """SPARQL over the graph: the catalogue, or a query of your own."""
    from fastraml.views.queries import QUERIES  # noqa: PLC0415 - query command only

    # `--list` and `--show` need neither a document nor pyoxigraph.
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
        return fail('query needs a FILE')
    built = build_or_report(args)
    return EXIT_INVALID if built is None else _run_sparql(args, built[0], text)


def _run_sparql(args: argparse.Namespace, graph: Graph, text: str) -> int:
    """Load the graph into a store and print whatever the query returns.

    `pyoxigraph` is an optional extra, imported here only.
    """
    import io  # noqa: PLC0415 - query execution only
    import json  # noqa: PLC0415 - query execution only

    try:
        import pyoxigraph  # noqa: PLC0415 - optional: a module-level import would make it required
    except ImportError:
        return fail('query needs an RDF store: install pyoxigraph')

    store = pyoxigraph.Store()
    store.load(io.StringIO('\n'.join(graph.to_ntriples())), format=pyoxigraph.RdfFormat.N_TRIPLES)
    result = store.query(text)

    # One result class per SPARQL form: `QuerySolutions` (SELECT),
    # `QueryTriples` (CONSTRUCT/DESCRIBE), `QueryBoolean` (ASK). The ASK result
    # is a wrapper convertible to `bool`, not a `bool`.
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
    return emit_document(args, ''.join(f'{row}\n' for row in rows))


def _query_text(args: argparse.Namespace) -> str | None:
    """The SPARQL to run: given, read from a file, or named in the catalogue."""
    from pathlib import Path  # noqa: PLC0415 - query files only

    from fastraml.views.queries import render  # noqa: PLC0415

    if args.sparql is not None:
        return str(args.sparql)
    if args.query_file is not None:
        return Path(args.query_file).read_text(encoding='utf-8')
    if args.named is not None:
        query = _named_query(args.named)
        return None if query is None else render(query)
    print('query needs one of -q, -Q or -n (or --list)', file=sys.stderr)
    return None


def _named_query(name: str) -> Query | None:
    """The catalogue query `name`, or `None` having said there is none."""
    from fastraml.views.queries import QUERIES  # noqa: PLC0415 - query command only

    query = QUERIES.get(name)
    if query is None:
        print(f'{name}: no such query; try --list', file=sys.stderr)
    return query


def _show(name: str) -> int:
    from fastraml.views.queries import render  # noqa: PLC0415 - query command only

    query = _named_query(name)
    if query is None:
        return EXIT_INVALID
    print(f'# {query.question}')
    print(render(query), end='')
    return EXIT_OK


def _term(term: Any) -> str | None:
    """One SPARQL solution binding as text.

    `Any` because `pyoxigraph` is optional and untyped here. `NamedNode`,
    `Literal` and `BlankNode` all have `.value`; `None` is an unbound
    `OPTIONAL` variable.
    """
    return None if term is None else str(term.value)
