"""The tree contract, as TypeScript declarations (docs/16-graph.md § 7).

Run `python -m fastraml.views.bindings typescript -o FILE`. The caller names the
destination; `-o -` writes stdout. `tests/unit/test_bindings.py` asserts that
the viewer's checked-in copy is what generation produces.

A consumer of `fastraml tree` has to know what the JSON holds. A hand-written
list goes stale the first time a facet is added to a kind, and it goes stale
*quietly*: a key the declarations omit still arrives, and a consumer that does
not read it looks exactly like a document that did not say it.

The output has two halves. `static/tree.d.ts` is hand-written and copied
verbatim: the metamodel aliases, `Ref` and the fixed records. Everything from
`ShapeType` on is generated from `ContractSchema`. Edit the static half for the
first; edit this module for the second.

`schema.py` derives the generated half from two sources, reading source rather
than importing it:

* **Which keys the projection emits** — from `tree.py`'s own AST. Every
  `_Projector` method builds a dict and the keys are literals: in the opening
  display, in `out[...] = ...`, or in the `for field in (...)` loops. Required
  and optional fall out of the same read, since a key assigned under an `if` is
  one the projection may omit.
* **What a kind's facets are, and their types** — from the `self.x: T = None`
  annotations in each kind's `__init__`. Python discards those at run time, so
  this reads them from the AST. The alternative is a table per kind, which is
  what is being avoided.

This module performs no source analysis. It maps the schema's annotations and
wire forms into TypeScript and declares the type of each *structural* key, which
no source analysis can infer from `out['operations']`. Key sets come from the
schema, so a key added to the projection and not declared here fails generation
by name.
"""

from __future__ import annotations

import argparse
import pathlib
from typing import TYPE_CHECKING, Final

from .output import write_rendered
from .schema import JSON_ONLY, Container, ContractSchema, Holds, Structural, contract_schema

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['typescript', 'typescript_conform', 'typescript_runtime']

#: How each structural kind is written in TypeScript. What every key *holds* is
#: declared once in `schema.py`; what is here is the spelling, which is all a
#: backend is for -- eight rows, where the table it replaced had one per key.
_LEAF: Final[dict[Holds, str]] = {
    Holds.JSON: 'Json',
    Holds.SHAPE_NODE: 'ShapeNode',
    Holds.SHAPE: 'Shape',
    Holds.REF: 'Ref',
}

#: The three scalar domains with a name of their own here. Anything else names
#: itself: `Address`, `ExactDecimal`, `SecuritySchemeType`.
_SCALAR: Final = {'str': 'string', 'bool': 'boolean', 'int': 'number'}


def _spelling(structure: Structural) -> str:
    """One structural kind as a TypeScript type."""
    if structure.holds is Holds.CONSTANT:
        # A literal type, which TypeScript has and the other two do not: the
        # value itself is written where a type is expected.
        return _literal(structure.constant)
    leaf = structure.alias or _LEAF.get(structure.holds) or _SCALAR.get(structure.of, structure.of)
    if not structure.alias:
        if structure.container is Container.LIST:
            leaf = f'{leaf}[]'
        elif structure.container is Container.MAP:
            leaf = f'Record<{structure.key or "string"}, {leaf}>'
        elif structure.container is Container.MAP_OF_MAP:
            raise LookupError(f'a map of maps needs a named alias; `{structure}` has none')
    return f'{leaf} | null' if structure.nullable else leaf


# -- the hand-declared half ----------------------------------------------------

#: The hand-written half of the binding. Nothing in it varies with the schema,
#: so it is a declaration file rather than a Python string: an editor reads it,
#: and a syntax error fails where it was written. Read the way `config.py` reads
#: `config.raml`.
_STATIC: Final = pathlib.Path(__file__).parent / 'static' / 'tree.d.ts'

#: The reading half. A module of its own because `tree.d.ts` is a *declaration*
#: file and cannot hold a value: the walk table is data, so this is where it
#: goes. Python and Go keep theirs beside the types, and this is the one place
#: the three artifacts are shaped differently -- for a reason that is
#: TypeScript's and not the contract's.
_RUNTIME: Final = pathlib.Path(__file__).parent / 'static' / 'walk.ts'

#: The conformance driver: one set of questions, answered in every language
#: (`conformance.py`). It holds no expectations, so nothing in it varies
#: with the schema and it is copied verbatim.
_CONFORM: Final = pathlib.Path(__file__).parent / 'static' / 'conform.ts'

#: What the viewer's formatter wraps at, so the generated table arrives already
#: formatted rather than failing `npm run check` on its first line.
_LINE_LENGTH: Final = 140


# -- generating ----------------------------------------------------------------


def typescript() -> str:
    """The whole declaration file: the hand-written half, then the derived one."""
    schema = contract_schema()
    parts = [_static(), _vocabularies(schema), _shape(schema), *_interfaces(schema)]
    return '\n'.join(parts)


def typescript_conform() -> str:
    """The conformance driver, verbatim. It holds no expectations."""
    return _CONFORM.read_text(encoding='utf-8')


def typescript_runtime() -> str:
    """The reading half: the hand-written walk, then the table it reads."""
    schema = contract_schema()
    static = _RUNTIME.read_text(encoding='utf-8').strip('\n')
    return '\n\n'.join([static, _envelope(schema), _children(schema)]) + '\n'


def _literal(value: str | int | None) -> str:
    """One constant in TypeScript's syntax, which is JSON's for both types."""
    return f"'{value}'" if isinstance(value, str) else str(value)


def _envelope(schema: ContractSchema) -> str:
    """The three constants a document always carries, as values.

    Declared as literal *types* in `tree.d.ts`, which a consumer cannot compare
    against. The envelope exists so a reader can refuse a representation it does
    not know (docs/16 § 6), and refusing needs the value.
    """
    names = {'format': 'FORMAT', 'format_version': 'FORMAT_VERSION', 'view': 'VIEW'}
    lines = [
        f'const {name} = {_literal(schema.structure_of("Document", key).constant)};' for key, name in names.items()
    ]
    lines.append("const RECURSION_TYPE = 'recursive';")
    return '\n'.join(lines)


def _children(schema: ContractSchema) -> str:
    """Where shapes sit under every record, as data the walk reads.

    This is the table a consumer writes by hand today, and the one place a
    hand-written one goes quietly stale: a kind that grows a shape-bearing facet
    arrives in the declarations and is silently not descended. Keys that cannot
    reach a shape are left out, so the table says only what a walk needs.
    """
    rows = ''
    for record, keys in schema.shape_bearing().items():
        entries = [_entry(key, held) for key, held in keys.items()]
        single = f'  {record}: [{", ".join(entries)}],'
        # Emitted already wrapped, because the viewer's gate formats this file
        # and a long line would fail it rather than be tidied.
        if len(single) <= _LINE_LENGTH:
            rows += f'{single}\n'
        else:
            rows += f'  {record}: [\n' + ''.join(f'    {entry},\n' for entry in entries) + '  ],\n'
    kinds = ''.join(f'  {kind.name!r}: {kind.model!r},\n' for kind in schema.shape_kinds)
    return (
        '/**\n'
        ' * Where a shape sits under each record: the key, how many, what the leaf\n'
        ' * is, and the record named where the leaf is one. Generated, so a facet\n'
        ' * that starts holding a shape starts being walked.\n'
        ' */\n'
        f'const CHILDREN: Record<string, [string, string, string, string][]> = {{\n{rows}}};\n'
        '\n'
        '/**\n'
        ' * The `type` discriminator to the record whose keys describe it. A `type`\n'
        ' * absent from here is a recursion marker or a document this file predates,\n'
        ' * and either way a walk stops.\n'
        ' */\n'
        f'const KINDS: Record<string, string> = {{\n{kinds}}};'
    )


def _entry(key: str, held: Structural) -> str:
    return f'[{key!r}, {held.container.value!r}, {held.holds.value!r}, {held.of!r}]'


def _static() -> str:
    """The hand-written half, verbatim.

    Read and not rendered: the file holds no substitution. It already contains
    `x-${string}`, which no format string could tell from a placeholder.
    """
    return _STATIC.read_text(encoding='utf-8').strip('\n') + '\n'


def _vocabularies(schema: ContractSchema) -> str:
    """Closed parser vocabularies as TypeScript literal unions."""
    return '\n'.join(
        f'export type {vocabulary.name} = {" | ".join(repr(value) for value in vocabulary.values)};'
        for vocabulary in schema.vocabularies
    )


def _interfaces(schema: ContractSchema) -> list[str]:
    """One interface per structural producer, keys checked against the source."""
    written: dict[str, list[str]] = {}
    for interface, name, optional in schema.produced_keys():
        written.setdefault(interface, []).append(_field(schema, interface, name, optional=optional))

    return [
        f'export interface {interface} {{\n' + '\n'.join(dict.fromkeys(lines)) + '\n}\n'
        for interface, lines in written.items()
    ]


def _field(schema: ContractSchema, owner: str, name: str, *, optional: bool) -> str:
    return f'  {name}{"?" if optional else ""}: {_spelling(schema.structure_of(owner, name))};'


def _shape(schema: ContractSchema) -> str:
    """A common shape record and one discriminator-derived interface per kind."""
    found, delegated = schema.shape_projection()

    base = [_field(schema, 'ShapeBase', name, optional=False) for name in found.required if name != 'type']
    # A delegate's keys are optional whatever it says of them: whether it runs
    # at all is the caller's condition, not the delegate's. JSON-schema fields
    # are the exception: they belong only to JsonShape below.
    base += [_field(schema, 'ShapeBase', name, optional=True) for name in found.optional if name not in JSON_ONLY]

    by_model = schema.kinds_by_model()

    variants: list[str] = []
    for model, names in by_model.items():
        lines = [f'  type: {" | ".join(repr(name) for name in names)};']
        for facet in schema.shape_facets[model]:
            note = ''
            if facet.wire_form == 'exact_decimal':
                note = ' // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"'
            spelling = _spelling(schema.facet_structure(facet))
            lines.append(f'  {facet.name}?: {spelling};{note}')
        if model == 'JsonShape':
            lines.extend(_field(schema, 'ShapeBase', name, optional=True) for name in delegated if name in JSON_ONLY)
        variants.append(f'export interface {model} extends ShapeBase {{\n' + '\n'.join(lines) + '\n}\n')

    variants.append(_recursion(schema))

    return (
        '/**\n'
        ' * Fields shared by every expanded type. Kind-specific facets live on the\n'
        ' * discriminated interfaces below.\n'
        ' *\n'
        ' * Numeric bounds use `ExactDecimal`; counts such as `max_items` remain\n'
        ' * numbers because they are bounded by memory rather than numeric precision.\n'
        ' */\n'
        f'export interface ShapeBase {{\n{"\n".join(base)}\n}}\n\n'
        + '\n'.join(variants)
        + f'\nexport type Shape = {" | ".join(by_model)};\n'
    )


def _recursion(schema: ContractSchema) -> str:
    """The recursion marker, as a shape rather than a record of its own.

    P9 builds a `RecursiveShape` and `shape()` projects it down the generic
    path, so a marker carries `id`, `name` and whatever `ShapeBase` fields the
    type it stands for had. It is not in `Shape`: `Shape` is what a declaration
    and a `projection` hold, and a marker is neither.

    Hand-declared. `schema.py` derives records by reading a `_Projector`
    method's AST, and `_Projector.recursion()` is a literal three-key dict that
    never runs, so generating from it declares three keys where seven ship
    (docs/16 § 6.1). `head` is hand-declared for a second reason:
    `shape()` writes it through a loop over `_BACK_POINTERS`, which no AST read
    resolves.
    """
    return (
        '/**\n'
        ' * A type that repeats here. Do not expand it; look `head` up instead.\n'
        ' *\n'
        ' * Spelled in `type` rather than a key of its own so a consumer that\n'
        ' * switches on `type` and has not handled it fails loudly.\n'
        ' */\n'
        'export interface Recursion extends ShapeBase {\n'
        "  type: 'recursive';\n"
        f'  head: {_spelling(schema.structure_of("ShapeBase", "head"))};\n'
        '}\n'
    )


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m fastraml.views.bindings typescript')
    parser.add_argument('-o', '--output', required=True, help='output file, or - for stdout')
    parser.add_argument('--conform', help='where to write the conformance driver; vendor it beside --runtime')
    parser.add_argument('--runtime', help='where to write the reading half; it imports the types from --output')
    options = parser.parse_args(arguments)
    write_rendered(options.output, typescript())
    if options.runtime:
        write_rendered(options.runtime, typescript_runtime())
    if options.conform:
        write_rendered(options.conform, typescript_conform())


if __name__ == '__main__':
    main()
