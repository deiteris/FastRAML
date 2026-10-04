"""Finding your way around a document: `list`, `refs`, `deps` and `show`."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.common import EXIT_INVALID, EXIT_OK, build_or_report

if TYPE_CHECKING:
    import argparse

    from fastraml.registry import Raml
    from fastraml.views.graph import Graph


def _show_type(args: argparse.Namespace) -> int:
    """The effective view: every inherited property in one place, with origins.

    Renders from the model; the graph only resolves `NAME`, and it omits
    facet detail (docs/16 § 4).
    """
    from fastraml.views.render import (  # noqa: PLC0415 - graph commands only
        Sources,
        render,
        render_endpoint,
        render_operation,
    )

    built = build_or_report(args)
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
        # A trait, resource type, payload, ...: no standalone effective form.
        # Report where it was written and how often it is applied instead.
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
    """The inventory of names `refs`, `deps` and `show` accept."""
    built = build_or_report(args)
    if built is None:
        return EXIT_INVALID
    graph, _ = built

    entries = graph.entries(args.kind)
    if args.pattern:
        wanted = args.pattern.casefold()
        entries = [entry for entry in entries if wanted in entry[1].casefold()]

    if not entries:
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
    """`refs` walks the edges backwards, `deps` forwards."""
    from fastraml.views.graph import TYPE_EDGES, USE_EDGES  # noqa: PLC0415 - graph commands only

    sites = getattr(args, 'sites', False)
    built = build_or_report(args, retain_text=sites)
    if built is None:
        return EXIT_INVALID
    graph, raml = built
    origin = _resolve(graph, args.name)
    if origin is None:
        return EXIT_INVALID
    if sites:
        return _sites(args, graph, raml, origin)

    reverse = args.command == 'refs'
    # `deps` follows type structure from a type, and use containment from
    # anything else: an endpoint has no type-structure edges of its own.
    forward = TYPE_EDGES if graph.kind_of(origin) == 'Type' else USE_EDGES
    routes = graph.walk(origin, USE_EDGES if reverse else forward, reverse=reverse, max_depth=args.depth)
    if args.kind:
        wanted = {kind.casefold() for kind in args.kind}
        routes = [route for route in routes if graph.kind_of(route.target).casefold() in wanted]
    paths = routes[: args.limit] if args.limit else routes

    for path in paths:
        # Printed in edge direction whichever way it was walked.
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
        # Fixed-width kind and position first; the variable-length route last.
        print(f'{graph.kind_of(path.target):<16} {where:<22} {route}')
    if len(paths) < len(routes):
        # Flush stdout first so redirected streams keep their order.
        sys.stdout.flush()
        print(f'... {len(routes) - len(paths)} more; raise --limit or narrow with --kind', file=sys.stderr)
    if not paths and not args.json:
        print(f'{args.name}: nothing found', file=sys.stderr)
    return EXIT_OK


def _sites(args: argparse.Namespace, graph: Graph, raml: Raml, origin: str) -> int:
    """`refs --sites`: where the name is written, from the occurrence index (docs/16 § 9)."""
    from fastraml.types.base import Property  # noqa: PLC0415 - graph commands only
    from fastraml.uris import relative_to  # noqa: PLC0415
    from fastraml.views.occurrences import build_occurrences  # noqa: PLC0415
    from fastraml.views.walk import workspace_of  # noqa: PLC0415

    entity = graph.entity_at(origin)
    # A property is a record; the index names its declaration.
    if isinstance(entity, Property):
        entity = entity.base
    target = getattr(entity, 'id', None)
    found = [] if target is None else build_occurrences(raml).of(target)
    root = workspace_of(raml)
    for occurrence in sorted(found, key=lambda o: (o.uri, o.span.line, o.span.column)):
        where = f'{relative_to(occurrence.uri, root)}:{occurrence.span.line}:{occurrence.span.column}'
        if args.json:
            import json  # noqa: PLC0415 - only JSON output needs the encoder

            print(json.dumps({'at': where, 'role': str(occurrence.role), 'kind': str(occurrence.kind)}))
        else:
            print(f'{where:<30} {occurrence.role}')
    if not found and not args.json:
        print(f'{args.name}: written nowhere the index records', file=sys.stderr)
    return EXIT_OK


def _owning_path(graph: Graph, iri: str) -> str:
    """The resource an operation hangs off, as its URI.

    Read over the `supportedOperation` edge: the operation node has no `path`.
    """
    for edge in graph.into(iri, ('supportedOperation',)):
        return graph.label(edge.subject)
    return ''


def _position_of(graph: Graph, iri: str) -> str:
    """`path:line` for a node, from its `definedIn` and `line` attributes."""
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
        # Suggest, never substitute: running the nearest name would answer a
        # different question.
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
