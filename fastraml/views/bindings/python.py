"""The tree contract, as Python type declarations — docs/16-graph.md § 11.11.

Run `python -m fastraml.views.bindings python -o FILE`. The caller names the
destination; `-o -` writes stdout. `tests/unit/test_bindings.py` asserts that
`contrib/raml-codegen`'s checked-in copy is what generation produces.

A Python consumer needs this as much as a TypeScript one does: `build_tree`
returns `Json`, so reading the wire format gives `dict[str, object]` and a
missing key surfaces at run time. The boundary a type checker fails to span is
the JSON, not the language.

The output has two halves. `static/tree.pyi` is hand-written and copied
verbatim: the imports, the aliases, `Ref` and the fixed records. Everything from
`ShapeType` on is generated from `ContractSchema`. Edit the static half for the
first; edit this module for the second.

This module performs no source analysis. `schema.py` does that. What is left
here is one job: spell each of the schema's structural kinds in this type
system. What every key *holds* is declared once in `schema.py`, because three
languages write `dict[str, Parameter]` three ways and agree entirely on what it
is; a key added to the projection and not declared there fails generation by
name, in all three backends at once.

What TypeScript spells for free and Python does not:

* **An optional key** is `NotRequired[T]`, per key. `total=False` would make
  every key optional and discard the required/optional split `schema.py`
  derives.
* **`$ref` is not an identifier**, so `Ref` needs `TypedDict`'s functional form.
* **A template literal type has no Python spelling.** `SecuritySchemeType`
  closes over the six the spec names and widens to `str` for the `x-` case,
  rather than pretending the whole vocabulary is closed.
* **A `TypedDict` subclass may not re-declare a key**, so `type` cannot narrow
  from `ShapeType` to `Literal['object']` by inheritance. `ShapeBase` omits it
  and each variant declares its own.
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
import re
import sys
import tempfile
from functools import cache
from typing import TYPE_CHECKING, Final

from .output import write_rendered
from .schema import JSON_ONLY, PRODUCES, Container, ContractSchema, Holds, Structural, contract_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import ModuleType

__all__ = ['python', 'python_conform', 'python_runtime', 'vendored']

#: How each structural kind is written in Python. Eight rows, where the table
#: they replaced had one row per key -- the key set and what each key holds are
#: facts about the contract, and `schema.py` states them once for all three
#: languages. What is left here is the spelling, which is all a backend is for.
_LEAF: Final[dict[Holds, str]] = {
    Holds.SCALAR: '',  # the domain names itself: `str`, `Address`, `ExactDecimal`
    Holds.VOCABULARY: '',
    Holds.RECORD: '',
    Holds.JSON: 'Json',
    Holds.SHAPE_NODE: 'ShapeNode',
    Holds.SHAPE: 'Shape',
    Holds.REF: 'Ref',
}


def _spelling(structure: Structural) -> str:
    """One structural kind as a Python annotation."""
    if structure.alias:
        return f'{structure.alias} | None' if structure.nullable else structure.alias
    if structure.holds is Holds.CONSTANT:
        return f'Literal[{structure.constant!r}]'
    leaf = _LEAF[structure.holds] or structure.of
    key = structure.key or 'str'
    if structure.container is Container.LIST:
        leaf = f'list[{leaf}]'
    elif structure.container is Container.MAP:
        leaf = f'dict[{key}, {leaf}]'
    elif structure.container is Container.MAP_OF_MAP:
        leaf = f'dict[SourceFile, dict[{key}, {leaf}]]'
    return f'{leaf} | None' if structure.nullable else leaf


# -- the hand-declared half ----------------------------------------------------

#: The hand-written half of the binding. Nothing in it varies with the schema,
#: so it is a file rather than a Python string: an editor reads it, and a syntax
#: error fails where it was written.
#:
#: A `.pyi` stub, because that is what it holds -- aliases, `TypedDict`s, no
#: runtime -- and because Python imports `.py` and `.pyw` only. The file is half
#: a contract: importing it would yield a fragment with no `Document` and no
#: vocabularies, and a consumer reading a key off a half-contract is the silent
#: failure this whole subsystem exists to prevent. As a stub it cannot be
#: imported at all.
_STATIC: Final = pathlib.Path(__file__).parent / 'static' / 'tree.pyi'

#: The runtime half: the metamodel's three constructs, and the walk over the
#: table `tree.py` carries. Copied verbatim, holding no generated character --
#: it reads `CHILDREN` from the module beside it, so the two are vendored
#: together and a stale pair fails to import rather than reading a short tree.
#:
#: A real `.py` and not a stub, because this half *is* runtime. Importing it
#: where it lives fails on `from .tree import CHILDREN`, since `static/` holds
#: only the `.pyi`; the half-contract problem the stub closes is closed here by
#: the import itself.
_RUNTIME: Final = pathlib.Path(__file__).parent / 'static' / 'walk.py'

#: The conformance driver: one set of questions, answered in every language
#: (`conformance.py`). It holds no expectations, so nothing in it varies
#: with the schema and it is copied verbatim.
_CONFORM: Final = pathlib.Path(__file__).parent / 'static' / 'conform.py'

#: The generated file is checked in and gate-compared as text, so it has to be
#: what `ruff format` would leave. Emitting it already wrapped is cheaper than
#: making the generator shell out to a formatter.
_LINE_LENGTH: Final = 120


# -- generating ----------------------------------------------------------------


def python() -> str:
    """The whole declaration module: the hand-written half, then the derived one."""
    schema = contract_schema()
    static = _static()
    # An alias whose own value is a string is not usable bare in a union:
    # `ShapeNode | None` would be `str | None` at run time, which raises. It is
    # still a name, so it is simply never "behind" -- anything mentioning one is
    # quoted whole.
    #
    # The fixed records moved into the static half with the preamble, so they
    # are behind *everything* now rather than last. That is the one way this
    # backend's output moved: `dict[str, Property]` needs no quoting any more,
    # because `Property` is a class by the time `ShapeBase` mentions it.
    behind = _Behind(set(_declared_in(static)) - set(_STRING_ALIASES.findall(static)))
    blocks = [static]

    blocks.append(_vocabularies(schema))
    behind.add(vocabulary.name for vocabulary in schema.vocabularies)

    for block in (*_shape(schema, behind), *_typed_dicts(schema, behind)):
        blocks.append(block)
        behind.add(_declared_in(block))

    blocks.append(_envelope(schema))
    blocks.append(_children(schema))
    blocks.append(_exports(schema))
    return '\n\n\n'.join(blocks) + '\n'


def python_conform() -> str:
    """The conformance driver, verbatim. It holds no expectations."""
    return _CONFORM.read_text(encoding='utf-8')


def python_runtime() -> str:
    """The runtime half, verbatim. Nothing in it varies with the schema."""
    return _RUNTIME.read_text(encoding='utf-8')


@cache
def vendored() -> ModuleType:
    """The two halves, imported the way a consumer vendors them: side by side.

    Not `raml_codegen.walk` and not a private path through this package: the
    corpus and the tests measure the artifact that is actually shipped, which is
    these two files in one package, and nothing else.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix='fastraml-tree-'))
    package = root / 'fastraml_tree_vendored'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'tree.py').write_text(python(), encoding='utf-8')
    (package / 'walk.py').write_text(python_runtime(), encoding='utf-8')
    sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f'{package.name}.walk')
    finally:
        sys.path.remove(str(root))


def _static() -> str:
    """The hand-written half, verbatim.

    Read and not rendered: the file holds no substitution.
    """
    return _STATIC.read_text(encoding='utf-8').strip('\n')


class _Behind:
    """The names already declared where the emitter currently stands.

    A quoted annotation is a forward reference and nothing else. The contract is
    cyclic, so some are unavoidable; quoting the rest would be noise, and getting
    it wrong is loud rather than silent -- the module fails to import.
    """

    __slots__ = ('names',)

    def __init__(self, names: Iterable[str]) -> None:
        self.names = set(names) | {'str', 'int', 'float', 'bool', 'None', 'list', 'dict', 'Literal'}

    def add(self, names: Iterable[str]) -> None:
        self.names.update(names)

    def covers(self, spelling: str) -> bool:
        # A `Literal` member is data, not a name: `Literal['date-only']` reaches
        # ahead of nothing.
        return all(name in self.names for name in _NAMES.findall(_STRINGS.sub('', spelling)))


#: A name declared at column zero: `X: TypeAlias = ...`, `X = TypedDict(...)`,
#: or `class X(...)`.
_DECLARED: Final = re.compile(r'^(?:class\s+)?([A-Za-z_]\w*)\s*(?::[^=\n]+)?[=(]', re.MULTILINE)
_NAMES: Final = re.compile(r'[A-Za-z_]\w*')
_STRINGS: Final = re.compile(r"'[^']*'")
_STRING_ALIASES: Final = re.compile(r"^([A-Za-z_]\w*): TypeAlias = '", re.MULTILINE)


def _declared_in(source: str) -> list[str]:
    return _DECLARED.findall(source)


def _vocabularies(schema: ContractSchema) -> str:
    """Closed parser vocabularies as `Literal` aliases."""
    return '\n'.join(_literal_alias(vocabulary.name, vocabulary.values) for vocabulary in schema.vocabularies)


def _literal_alias(name: str, values: tuple[str, ...]) -> str:
    single = f'{name}: TypeAlias = Literal[{", ".join(repr(value) for value in values)}]'
    if len(single) <= _LINE_LENGTH:
        return single
    return f'{name}: TypeAlias = Literal[\n' + '\n'.join(f'    {value!r},' for value in values) + '\n]'


def _typed_dicts(schema: ContractSchema, behind: _Behind) -> list[str]:
    """One TypedDict per structural producer, keys checked against the source.

    Every one of them is emitted against the same `behind`: they reference each
    other, so declaring the earlier ones first would only move which are quoted.
    """
    written: dict[str, list[str]] = {}
    for name, key, optional in schema.produced_keys():
        written.setdefault(name, []).append(_field(schema, name, key, behind, optional=optional))

    return [f'class {name}(TypedDict):\n' + '\n'.join(dict.fromkeys(lines)) for name, lines in written.items()]


def _field(schema: ContractSchema, owner: str, name: str, behind: _Behind, *, optional: bool) -> str:
    spelling = _spelling(schema.structure_of(owner, name))
    return f'    {name}: {_annotation(spelling, behind, optional=optional)}'


def _annotation(spelling: str, behind: _Behind, *, optional: bool) -> str:
    """One field annotation, quoted only where it reaches ahead of itself.

    `NotRequired` stays outside the quotes so `TypedDict` can still see it; a
    string hiding it is what makes `__required_keys__` wrong (module docstring).
    """
    body = spelling if behind.covers(spelling) else f'{spelling!r}'
    return f'NotRequired[{body}]' if optional else body


def _shape(schema: ContractSchema, behind: _Behind) -> list[str]:
    """A common shape record and one discriminator-derived TypedDict per kind."""
    found, delegated = schema.shape_projection()

    # `type` is left off the base and declared on each variant: a TypedDict
    # subclass may not re-declare a key, so this is the only way the
    # discriminator narrows.
    base = [_field(schema, 'ShapeBase', name, behind, optional=False) for name in found.required if name != 'type']
    # A delegate's keys are optional whatever it says of them: whether it runs
    # at all is the caller's condition, not the delegate's. JSON-schema fields
    # are the exception: they belong only to JsonShape below.
    base += [
        _field(schema, 'ShapeBase', name, behind, optional=True) for name in found.optional if name not in JSON_ONLY
    ]

    by_model = schema.kinds_by_model()

    variants: list[str] = []
    for model, names in by_model.items():
        lines = [f'    type: Literal[{", ".join(repr(name) for name in names)}]']
        for facet in schema.shape_facets[model]:
            note = ''
            if facet.wire_form == 'exact_decimal':
                note = "  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'"
            spelling = _spelling(schema.facet_structure(facet))
            lines.append(f'    {facet.name}: {_annotation(spelling, behind, optional=True)}{note}')
        if model == 'JsonShape':
            lines.extend(
                _field(schema, 'ShapeBase', name, behind, optional=True) for name in delegated if name in JSON_ONLY
            )
        variants.append(f'class {model}(ShapeBase):\n' + '\n'.join(lines))

    head = (
        'class ShapeBase(TypedDict):\n'
        '    """Fields shared by every expanded type.\n'
        '\n'
        '    Kind-specific facets live on the TypedDicts below. `type` is not here:\n'
        '    a TypedDict subclass may not re-declare a key, so each variant declares\n'
        '    its own discriminator.\n'
        '\n'
        '    Numeric bounds use `ExactDecimal`; counts such as `max_items` remain\n'
        '    numbers because they are bounded by memory rather than numeric precision.\n'
        '    """\n'
        '\n'
        f'{"\n".join(base)}'
    )
    return [head, *variants, _union_alias('Shape', tuple(by_model)), _recursion(schema)]


def _recursion(schema: ContractSchema) -> str:
    """The recursion marker, as a shape rather than a record of its own.

    P9 builds a `RecursiveShape` and `shape()` projects it down the generic
    path, so a marker carries `id`, `name` and whatever `ShapeBase` fields the
    type it stands for had. It is not a member of `Shape`: `Shape` is what a
    declaration and a `projection` hold, and a marker is neither.

    Hand-declared. `schema.py` derives records by reading a `_Projector`
    method's AST, and `_Projector.recursion()` is a literal three-key dict that
    never runs, so generating from it declares three keys where seven ship
    (docs/16 section 11.11c). `head` is hand-declared for a second reason:
    `shape()` writes it through a loop over `_BACK_POINTERS`, which no AST read
    resolves.
    `name` is not re-declared: a TypedDict subclass may not, and `ShapeBase`
    already carries it.
    """
    return (
        'class Recursion(ShapeBase):\n'
        '    """A type that repeats here. Do not expand it; look `head` up instead.\n'
        '\n'
        '    Spelled in `type` rather than a key of its own, so a consumer that\n'
        '    switches on `type` and has not handled it fails loudly.\n'
        '    """\n'
        '\n'
        "    type: Literal['recursive']\n"
        f'    head: {_spelling(schema.structure_of("ShapeBase", "head"))}'
    )


def _union_alias(name: str, members: tuple[str, ...]) -> str:
    single = f'{name}: TypeAlias = {" | ".join(members)}'
    if len(single) <= _LINE_LENGTH:
        return single
    body = '\n'.join(f'    {member}' if index == 0 else f'    | {member}' for index, member in enumerate(members))
    return f'{name}: TypeAlias = (\n{body}\n)'


def _envelope(schema: ContractSchema) -> str:
    """The three constants a document always carries, as values.

    Declared as `Literal` types above, which a consumer cannot compare against.
    The envelope exists so a reader can refuse a representation it does not
    know (docs/16 § 11.9), and refusing needs the value.
    """
    names = {'format': 'FORMAT', 'format_version': 'FORMAT_VERSION', 'view': 'VIEW'}
    return '\n'.join(
        f'{name}: Final = {schema.structure_of("Document", key).constant!r}' for key, name in names.items()
    )


def _children(schema: ContractSchema) -> str:
    """Where shapes sit under every record, as data a walk reads.

    This is the table a consumer writes by hand today, and the one place a
    hand-written one goes quietly stale: a kind that grows a shape-bearing facet
    arrives in the declarations above and is silently not descended. Keys that
    cannot reach a shape are left out, so the table says only what a walk needs.
    """
    bearing = schema.shape_bearing()
    rows: list[str] = []
    for record, keys in bearing.items():
        entries = [
            f'({key!r}, {structure.container.value!r}, {structure.holds.value!r}, {structure.of!r})'
            for key, structure in keys.items()
        ]
        # What `ruff format` would leave: collapsed while it fits, exploded
        # otherwise. The file is checked in and gate-compared as text.
        # A one-element tuple keeps its comma because the syntax needs it; a
        # longer one must not have one, or the magic trailing comma explodes the
        # line that was about to fit.
        inner = f'{entries[0]},' if len(entries) == 1 else ', '.join(entries)
        single = f'    {record!r}: ({inner}),'
        if len(single) <= _LINE_LENGTH:
            rows.append(f'{single}\n')
        else:
            body = ''.join(f'        {entry},\n' for entry in entries)
            rows.append(f'    {record!r}: (\n{body}    ),\n')
    kinds = ''.join(f'    {kind.name!r}: {kind.model!r},\n' for kind in schema.shape_kinds)
    return (
        '#: Where a shape sits under each record: the key, how many, what the\n'
        '#: leaf is, and the record named where the leaf is one. Generated, so a\n'
        '#: facet that starts holding a shape starts being walked.\n'
        f'CHILDREN: Final[dict[str, tuple[tuple[str, str, str, str], ...]]] = {{\n{"".join(rows)}}}\n'
        '\n'
        '#: The `type` discriminator to the record whose keys describe it. A `type`\n'
        '#: absent from here is a recursion marker or a document this file predates,\n'
        '#: and either way a walk stops.\n'
        f'KINDS: Final[dict[str, str]] = {{\n{kinds}}}'
    )


def _exports(schema: ContractSchema) -> str:
    """Every name this module declares, so a star import is the whole contract."""
    names = [
        'Address',
        'BodiesByMediaType',
        'DeclarationName',
        'DocumentationItem',
        'EndpointPath',
        'EndpointsByPath',
        'ExactDecimal',
        'Json',
        'JsonObject',
        'MediaType',
        'OperationsByMethod',
        'Parameter',
        'ParameterBinding',
        'PatternProperty',
        'Property',
        'Protocol',
        'Ref',
        'ResponsesByStatus',
        'SecuritySchemeDeclarations',
        'SecuritySchemeDeclarationsByFile',
        'SecuritySchemeType',
        'SecuritySetting',
        'SecuritySettings',
        'Shape',
        'ShapeBase',
        'ShapeDeclarations',
        'ShapeDeclarationsByFile',
        'ShapeNode',
        'SourceFile',
        'StatusCode',
        *(vocabulary.name for vocabulary in schema.vocabularies),
        *dict.fromkeys(PRODUCES.values()),
        *dict.fromkeys(kind.model for kind in schema.shape_kinds),
    ]
    body = '\n'.join(f'    {name!r},' for name in sorted(set(names)))
    return f'__all__ = [\n{body}\n]'


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m fastraml.views.bindings python')
    parser.add_argument('-o', '--output', required=True, help='output file, or - for stdout')
    parser.add_argument(
        '--runtime', help='where to write the reading half; vendor it beside --output, which it imports'
    )
    parser.add_argument('--conform', help='where to write the conformance driver; vendor it beside --runtime')
    options = parser.parse_args(arguments)
    write_rendered(options.output, python())
    if options.runtime:
        write_rendered(options.runtime, python_runtime())
    if options.conform:
        write_rendered(options.conform, python_conform())


if __name__ == '__main__':
    main()
