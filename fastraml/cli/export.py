"""The document-producing verbs: `graph`, `tree`, and `convert` to OpenAPI,
JSON Schema or RAML.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fastraml.cli.common import EXIT_INVALID, build_or_report, emit_document, parse_options, parse_or_report

if TYPE_CHECKING:
    import argparse


def _graph(args: argparse.Namespace) -> int:
    built = build_or_report(args)
    if built is None:
        return EXIT_INVALID
    graph, _ = built
    if args.format == 'json':
        import json  # noqa: PLC0415 - only JSON output needs the encoder

        return emit_document(args, json.dumps(graph.to_json(), indent=2) + '\n')
    emit = {'nt': graph.to_ntriples, 'turtle': graph.to_turtle, 'dot': graph.to_dot}[args.format]
    return emit_document(args, ''.join(f'{line}\n' for line in emit()))


def _openapi(args: argparse.Namespace) -> int:
    """Write the effective API as an OpenAPI 3.0 document."""
    raml = parse_or_report(args)
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
    return emit_document(args, text, dropped)


def _tree(args: argparse.Namespace) -> int:
    """The effective document as a tree, every reference an address.

    The same walk assigns addresses for `graph`, so an address printed here
    names the node `graph` prints (docs/16 § 6).
    """
    import json  # noqa: PLC0415 - only this verb needs the encoder

    from fastraml.views.tree import build_tree, positions_of  # noqa: PLC0415

    # No graph: `build_tree` assigns its own addresses.
    raml = parse_or_report(args)
    if raml is None:
        return EXIT_INVALID
    payload = positions_of(raml) if args.positions else build_tree(raml)
    # No `sort_keys`: declaration order is an invariant (docs/02 § 4).
    return emit_document(args, json.dumps(payload, indent=2) + '\n')


def _convert(args: argparse.Namespace) -> int:
    if args.target == 'openapi':
        return _openapi(args)
    if args.target == 'jsonschema':
        return _convert_jsonschema(args)
    return _convert_raml(args)


def _convert_jsonschema(args: argparse.Namespace) -> int:
    """Export one effective type, reporting any semantics JSON Schema cannot carry."""
    import json  # noqa: PLC0415 - this target writes JSON only

    from fastraml.parser.fragments import APIFragment, DataTypeFragment, Library  # noqa: PLC0415
    from fastraml.views.jsonschema import to_json_schema  # noqa: PLC0415

    raml = parse_or_report(args)
    if raml is None:
        return EXIT_INVALID
    if isinstance(raml.entry_point, DataTypeFragment):
        if args.name is not None:
            print('convert jsonschema: a DataType fragment does not take TYPE', file=sys.stderr)
            return EXIT_INVALID
        base = raml.entry_point.shape
    elif isinstance(raml.entry_point, (APIFragment, Library)):
        if args.name is None:
            print('convert jsonschema: TYPE is required for an API or Library', file=sys.stderr)
            return EXIT_INVALID
        base = raml.types_in(raml.location).get(args.name)
    else:
        print('convert jsonschema: expected an API, Library, or DataType', file=sys.stderr)
        return EXIT_INVALID
    if base is None:
        print(f'convert jsonschema: no type {args.name!r} in {args.files[0]}', file=sys.stderr)
        return EXIT_INVALID
    document, dropped = to_json_schema(base)
    return emit_document(args, json.dumps(document, indent=2, ensure_ascii=False) + '\n', dropped)


def _convert_raml(args: argparse.Namespace) -> int:
    """Parse an external schema through the ordinary include loader, then export it."""
    import json  # noqa: PLC0415 - only the schema path needs quoting
    from pathlib import Path  # noqa: PLC0415

    from fastraml.errors import RamlError  # noqa: PLC0415
    from fastraml.parser.entry import parse_from_string  # noqa: PLC0415
    from fastraml.parser.fragments import DataTypeFragment  # noqa: PLC0415
    from fastraml.types.jsonschema_ import JsonShape  # noqa: PLC0415
    from fastraml.views.raml import to_raml  # noqa: PLC0415

    path = Path(args.file).absolute()
    if path.suffix.lower() != '.json':
        print(f'{args.file}: expected a .json schema', file=sys.stderr)
        return EXIT_INVALID
    source = f'#%RAML 1.0 DataType\ntype: !include {json.dumps(path.name)}\n'
    try:
        parsed = parse_from_string(
            source, file_name='_schema.raml', base_dir=path.parent, options=parse_options(args, validate=False)
        )
        entry = parsed.entry_point
        if not isinstance(entry, DataTypeFragment) or entry.shape is None:
            print(f'{args.file}: expected a JSON Schema DataType', file=sys.stderr)
            return EXIT_INVALID
        schema = entry.shape.shape
        if not isinstance(schema, JsonShape):
            print(f'{args.file}: expected a JSON Schema type', file=sys.stderr)
            return EXIT_INVALID
        text = to_raml(schema, name=path.stem)
    except (RamlError, ValueError) as err:
        print(f'{args.file}: {err}', file=sys.stderr)
        return EXIT_INVALID
    return emit_document(args, text)
