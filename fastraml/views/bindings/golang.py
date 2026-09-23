"""The tree contract, as Go declarations (docs/16-graph.md § 7).

Run `python -m fastraml.views.bindings golang -o FILE`. The caller names the
destination; `-o -` writes stdout, and `-p NAME` sets the package clause.

The output has two halves. `static/tree.go` is hand-written and copied verbatim
apart from its package clause: the metamodel types, their decoding, and the
fixed records. Everything from `ShapeType` on is generated from
`ContractSchema`. Edit the static half for the first; edit this module for the
second.

This module performs no source analysis. `schema.py` does that. What is left
here is the same two jobs the other backends have: map the schema's annotations
and wire forms into Go, and declare the Go type of each *structural* key, which
no source analysis can infer from `out['operations']`. Key sets come from the
schema, so a key added to the projection and not declared here fails generation
by name.

How Go spells what the other two backends get from their type systems:

* **A union.** `ShapeNode` is a struct with one field per arm. `Shape` is an
  interface; `shapeOf` looks a variant up in the generated `shapeKinds` table.
  Both live in the static half, since neither holds anything derived.
* **A literal type.** A closed vocabulary becomes a defined string type and one
  constant per member. `SecuritySchemeType` is open for free: `x-<anything>` is
  a value of a type nobody named.
* **An optional key.** A nil-able type plus `,omitzero`. A scalar has to be a
  pointer to be nil-able, so `min_length` is `*int`; a slice, map or pointer is
  left alone. `omitzero` and not `omitempty`, because the two differ on an empty
  list and the tree emits those — see `_field`.
* **A map's order.** Go maps discard it, so every map whose keys are data is
  `*orderedmap.OrderedMap`. That is the only dependency beyond the standard
  library, and it exists because the tree preserves declaration order
  (invariant I8, docs/02 § 4).
* **Arbitrary JSON.** `Json` is raw bytes, not `any`, which keeps the author's
  key order and keeps an absent key distinct from a null one. It carries
  `Decode`, `Object` and `IsNull`.

There is no checked-in Go consumer, so there is no golden file.
`tests/unit/test_bindings.py` compiles the output instead and decodes two real
documents through it.
"""

from __future__ import annotations

import argparse
import pathlib
import re
from typing import TYPE_CHECKING, Final

from .output import write_rendered
from .schema import JSON_ONLY, Container, ContractSchema, Holds, Structural, contract_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ['golang', 'golang_conform', 'golang_runtime']

#: How each structural kind is written in Go. What every key *holds* is declared
#: once in `schema.py`; what is here is the spelling, which is all a backend is
#: for. Go needs more of it than the other two, for the reason it needs more of
#: everything: no literal type, no optional field, and no ordered map.
_LEAF: Final[dict[Holds, str]] = {
    Holds.JSON: 'Json',
    Holds.SHAPE_NODE: 'ShapeNode',
    #: Always an expanded shape and never a link, but Go cannot narrow a union.
    Holds.SHAPE: '*ShapeNode',
    Holds.REF: 'Ref',
}

_SCALAR: Final = {'str': 'string', 'bool': 'bool', 'int': 'int'}

#: Every map whose keys are data is an ordered map, and an ordered map is a named
#: alias in `static/tree.go` -- Go has no inline spelling for one. Where the
#: contract names the alias itself, `Structural.alias` carries it and this table
#: is not reached; these are the ones only Go needs, because the other two
#: languages write those maps inline.
_MAPS: Final[dict[tuple[Holds, str], str]] = {
    (Holds.RECORD, 'Parameter'): 'ParametersByName',
    (Holds.RECORD, 'Example'): 'ExamplesByName',
    (Holds.RECORD, 'Property'): 'PropertiesByName',
    (Holds.RECORD, 'PatternProperty'): 'PatternPropertiesByPattern',
    (Holds.JSON, ''): 'FacetValuesByName',
}


def _spelling(structure: Structural) -> str:
    """One structural kind as a Go type."""
    if structure.holds is Holds.CONSTANT:
        # Go has no literal type. The struct field is the value's type, and the
        # constants below the preamble's aliases carry the values.
        return 'string' if isinstance(structure.constant, str) else 'int'
    if structure.container in {Container.MAP, Container.MAP_OF_MAP}:
        alias = structure.alias or _MAPS.get((structure.holds, structure.of))
        if alias is None:
            raise LookupError(f'a map needs a named ordered-map alias; `{structure}` has none (see _MAPS)')
        return alias
    leaf = structure.alias or _LEAF.get(structure.holds) or _SCALAR.get(structure.of, structure.of)
    if structure.container is Container.LIST:
        return f'[]{leaf}'
    # A struct has to be a pointer to be nil-able, and a record under a nullable
    # or an optional key is exactly that. `_optional` leaves an existing `*`
    # alone, so adding it here is not doubled.
    if structure.holds is Holds.RECORD or structure.nullable:
        return _optional(leaf)
    return leaf


#: Go's spelling of the words that are acronyms. Nothing here is a choice about
#: the contract; `golint` would rewrite `Id` and `Uri` either way.
_INITIALISMS: Final = frozenset({'API', 'HTTP', 'HTTPS', 'ID', 'JSON', 'URI', 'URL', 'XML'})

#: Type names that already admit `nil`, so `_optional` must not add a `*`. The
#: `*` and `[]` cases are recognised by their spelling; these are the aliases
#: whose right-hand side is a pointer, plus the one interface.
_NILABLE: Final = frozenset(
    {
        'Json',
        'JsonObject',
        'Shape',
        'ShapeDeclarations',
        'ShapeDeclarationsByFile',
        'SecuritySchemeDeclarations',
        'SecuritySchemeDeclarationsByFile',
        'EndpointsByPath',
        'OperationsByMethod',
        'ResponsesByStatus',
        'BodiesByMediaType',
        'SecuritySettings',
        'ParametersByName',
        'ExamplesByName',
        'PropertiesByName',
        'PatternPropertiesByPattern',
        'FacetValuesByName',
    }
)


# -- the hand-declared half ----------------------------------------------------

#: A one-line doc comment per struct, so the generated file reads like Go rather
#: than like a table. A struct absent from here is documented by its name alone.
_DOC: Final = {
    'Document': 'Document is the whole tree: one API, resolved and unwrapped.',
    'EntryPoint': 'EntryPoint is the root fragment the parse started from.',
    'SecurityScheme': 'SecurityScheme is one declared scheme and its settings.',
    'DescribedBy': 'DescribedBy is the request and response surface a scheme adds to what it secures.',
    'Endpoint': 'Endpoint is one resource, at its full path.',
    'Operation': 'Operation is one method on an endpoint, with its traits already merged in.',
    'Response': 'Response is one status code of an operation.',
    'SecuredBy': 'SecuredBy is one entry of a securedBy list, resolved to what it names.',
    'Applied': 'Applied is one annotation applied at a site.',
    'DocumentAnnotation': 'DocumentAnnotation is one annotation, listed with the target it was applied to.',
    'Example': 'Example is one example value and what the author said about it.',
}


#: The hand-written half of the binding, verbatim. It is a `.go` file because
#: nothing in it varies with the schema: `gofmt` checks it, an editor highlights
#: it, and a syntax error fails where it was written rather than three layers
#: downstream. Read the way `config.py` reads `config.raml`.
_STATIC: Final = pathlib.Path(__file__).parent / 'static' / 'tree.go'

#: The ordered-map aliases in `static/tree.go` whose element is already a
#: pointer. Everything else holds its `ShapeNode` by value and is addressed.
_POINTER_ELEMENTS: Final = frozenset({'BodiesByMediaType'})

#: The package clause the static half is written with, so the file is valid Go
#: rather than Go with a hole in it. A `{placeholder}` would cost exactly the
#: thing the file is there for.
_STATIC_PACKAGE: Final = 'package tree'

#: The reading half: the walk over the contract, and the descent generated from
#: the same table the other two backends render as data. A separate file in the
#: same package, so a consumer that only decodes need not take it.
_RUNTIME: Final = pathlib.Path(__file__).parent / 'static' / 'walk.go'

#: The conformance driver: one set of questions, answered in every language
#: (`conformance.py`). It holds no expectations, so nothing in it varies
#: with the schema and it is copied verbatim.
_CONFORM: Final = pathlib.Path(__file__).parent / 'static' / 'conform.go'

#: The import path the driver is written with, so the file compiles where it
#: lives rather than being Go with a hole in it.
_CONFORM_IMPORT: Final = 'conformance/tree'

#: What a Go identifier may not be split across. Anything else separates words.
_WORDS: Final = re.compile(r'[^0-9A-Za-z]+')


# -- generating ----------------------------------------------------------------


def golang(package: str = 'tree') -> str:
    """The whole declaration file: the hand-written half, then the derived one."""
    schema = contract_schema()
    blocks = [
        _static(package),
        _envelope(schema),
        _vocabularies(schema),
        *_shape(schema),
        *_structs(schema),
    ]
    return '\n\n'.join(blocks) + '\n'


def golang_conform(import_path: str = 'conformance/tree') -> str:
    """The conformance driver, with its import path set.

    The one substitution it takes, and the same argument the package clause
    takes: the file is real Go, and a `{placeholder}` is what stops Go's own
    tools reading it.
    """
    text = _CONFORM.read_text(encoding='utf-8')
    if _CONFORM_IMPORT not in text:
        raise LookupError(f'{_CONFORM} does not import `{_CONFORM_IMPORT}`')
    return text.replace(_CONFORM_IMPORT, import_path, 1)


def golang_runtime(package: str = 'tree') -> str:
    """The reading half: the hand-written walk, then the generated descent."""
    schema = contract_schema()
    return '\n\n'.join([_runtime_static(package), *_walk(schema)]) + '\n'


def _runtime_static(package: str) -> str:
    """The hand-written reading, with its package clause set.

    The one substitution in either half, for the reason `_static` gives: the
    file is real Go so that Go's own tools read it, and a `{placeholder}` is
    exactly what stops them.
    """
    text = _RUNTIME.read_text(encoding='utf-8').strip('\n')
    if _STATIC_PACKAGE not in text:
        raise LookupError(f'{_RUNTIME} does not declare `{_STATIC_PACKAGE}`')
    return text.replace(_STATIC_PACKAGE, f'package {package}', 1)


def _envelope(schema: ContractSchema) -> str:
    """The three constants a Document always carries.

    Go has no literal type, so the struct fields are plain `string` and `int`
    and these carry the values. Generated, because the contract states them and
    a hand-written copy is a second place for them to be wrong.
    """
    rows = [
        (_go_name(key), f'= {_go_literal(schema.structure_of("Document", key).constant)}', '', '')
        for key in ('format', 'format_version', 'view')
    ]
    return (
        '// The three constants a Document always carries, and the values a reader\n'
        '// refuses a document for not matching.\n'
        'const (\n' + '\n'.join(_aligned(rows)) + '\n)'
    )


def _go_literal(value: str | int | None) -> str:
    """One constant in Go's syntax: a double-quoted string, or a bare int."""
    return f'"{value}"' if isinstance(value, str) else str(value)


def _walk(schema: ContractSchema) -> list[str]:
    """One `childShapes` method per record, and the switch over shape kinds.

    The other two backends render `shape_bearing()` as a table their runtime
    indexes by string. Go cannot index a struct, so the same fact is code here.
    Nothing about which keys are in it is decided here.
    """
    bearing = schema.shape_bearing()
    kinds = {kind.model for kind in schema.shape_kinds}
    blocks = [
        _child_method(schema, record, keys)
        for record, keys in bearing.items()
        if record not in kinds and record != 'ShapeBase'
    ]
    blocks.append(_child_method(schema, 'ShapeBase', bearing['ShapeBase']))
    blocks.append(_shape_children(schema, bearing))
    return blocks


def _child_method(schema: ContractSchema, record: str, keys: dict[str, Structural]) -> str:
    body = ''.join(_descend(schema, record, key, structure) for key, structure in keys.items())
    return (
        f'// childShapes appends every shape node a {record} holds directly.\n'
        f'func (v *{record}) childShapes(out []*ShapeNode) []*ShapeNode {{\n{body}\treturn out\n}}'
    )


def _shape_children(schema: ContractSchema, bearing: dict[str, dict[str, Structural]]) -> str:
    arms = []
    for model in dict.fromkeys(kind.model for kind in schema.shape_kinds):
        body = ''.join(_descend(schema, model, key, structure) for key, structure in bearing.get(model, {}).items())
        arm = f'\tcase *{model}:\n\t\tout = v.ShapeBase.childShapes(out)\n' + _indent(body)
        arms.append(arm.rstrip())
    return (
        '// shapeChildren appends every shape node one expanded type holds directly.\n'
        '// A kind with no shape-bearing facet of its own contributes only what every\n'
        '// shape carries.\n'
        'func shapeChildren(shape Shape, out []*ShapeNode) []*ShapeNode {\n'
        '\tswitch v := shape.(type) {\n' + '\n'.join(arms) + '\n\t}\n\treturn out\n}'
    )


def _indent(body: str) -> str:
    return ''.join(f'\t{line}\n' if line else '\n' for line in body.split('\n')[:-1])


def _descend(schema: ContractSchema, record: str, key: str, structure: Structural) -> str:
    """One key's descent, in the idiom its container needs.

    Four idioms, one per container, and the only variation inside them is
    whether the element is already a pointer. Every map whose values are records
    holds them by value -- `EndpointsByPath` is `[EndpointPath, Endpoint]`, not
    `[EndpointPath, *Endpoint]` -- so those are addressed rather than nil-checked.
    """
    field = f'v.{_go_name(key)}'
    if structure.container is Container.ONE:
        # A record or a nullable node under a single key is a pointer either
        # way; nothing else reaches here.
        return f'\tif {field} != nil {{\n\t\t{_take(structure, field)}\n\t}}\n'
    if structure.container is Container.LIST:
        return f'\tfor i := range {field} {{\n\t\t{_take(structure, f"&{field}[i]")}\n\t}}\n'
    element = 'pair.Value' if structure.container is Container.MAP else 'inner.Value'
    if structure.holds is Holds.RECORD:
        body = _take(structure, element)
    else:
        held = _pointer(schema, record, key, element)
        body = (
            _take(structure, held) if held.startswith('&') else f'if {held} != nil {{\n\t{_take(structure, held)}\n}}'
        )
    if structure.container is Container.MAP_OF_MAP:
        body = _loop('inner', 'pair.Value', body)
    return _shift(_loop('pair', field, body) + '\n')


def _loop(name: str, over: str, body: str) -> str:
    return f'for {name} := {over}.Oldest(); {name} != nil; {name} = {name}.Next() {{\n{_shift(body)}\n}}'


def _take(structure: Structural, element: str) -> str:
    if structure.holds is Holds.RECORD:
        return f'out = {element}.childShapes(out)'
    return f'out = append(out, {element})'


def _shift(body: str) -> str:
    return '\n'.join(f'\t{line}' if line else '' for line in body.split('\n'))


def _pointer(schema: ContractSchema, record: str, key: str, value: str) -> str:
    """Whether a map's innermost shape-node value is already a pointer.

    `BodiesByMediaType` holds `*ShapeNode`; `ShapeDeclarations` holds a bare
    `ShapeNode`, which has to be addressed. Both are aliases `static/tree.go`
    declares, and `TestTheGoMapElementsAreDeclaredAsListed` checks this list
    against that file rather than this reading it at generation time.
    """
    return value if schema.structure_of(record, key).alias in _POINTER_ELEMENTS else f'&{value}'


def _static(package: str) -> str:
    """The hand-written half, with its package clause set to the caller's.

    A `replace` and not a template: the package clause is the one substitution
    in the file, and it is written as real Go so `gofmt` can still read it.
    """
    contract = _STATIC.read_text(encoding='utf-8').strip('\n')
    if _STATIC_PACKAGE not in contract:
        raise LookupError(f'{_STATIC.name} does not declare `{_STATIC_PACKAGE}`')
    return contract.replace(_STATIC_PACKAGE, f'package {package}')


def _vocabularies(schema: ContractSchema) -> str:
    """Closed parser vocabularies as defined string types and named constants."""
    blocks = []
    for vocabulary in schema.vocabularies:
        name = vocabulary.name
        constants = _aligned(
            (f'{name}{_go_name(value)}', name, f'= {value!r}'.replace("'", '"'), '') for value in vocabulary.values
        )
        blocks.append(
            f'// {name} is a closed vocabulary. Every value the tree can carry is named below.\n'
            f'type {name} string\n\nconst (\n' + '\n'.join(constants) + '\n)'
        )
    return '\n\n'.join(blocks)


def _structs(schema: ContractSchema) -> list[str]:
    """One struct per structural producer, keys checked against the source."""
    written: dict[str, list[tuple[str, str, str, str]]] = {}
    for name, key, optional in schema.produced_keys():
        written.setdefault(name, []).append(_row(schema, name, key, optional=optional))

    return [_struct(name, _DOC.get(name, f'{name} is one node of the tree.'), rows) for name, rows in written.items()]


def _row(schema: ContractSchema, owner: str, key: str, *, optional: bool) -> tuple[str, str, str, str]:
    return _field(key, _spelling(schema.structure_of(owner, key)), optional=optional)


def _field(key: str, spelling: str, *, optional: bool, note: str = '') -> tuple[str, str, str, str]:
    # `omitzero` and not `omitempty`: the two differ on an empty list, which the
    # tree does emit -- `annotations`, `media_types`, `protocols` and
    # `secured_by` arrive as `[]` over the corpus. `omitempty` would write each
    # of them back as an absent key.
    tag = f'`json:"{key},omitzero"`' if optional else f'`json:"{key}"`'
    return _go_name(key), _optional(spelling) if optional else spelling, tag, note


def _optional(spelling: str) -> str:
    """A nil-able spelling of `spelling`, so an absent key is distinguishable.

    A slice, a map, a pointer and an interface already are; a scalar or a struct
    has to become a pointer. The tag alone would not do it: the zero `string` is
    `""`, so `omitzero` on a bare one omits the empty string as well as the
    absent key.
    """
    if spelling.startswith(('*', '[]', 'map[')) or spelling in _NILABLE:
        return spelling
    return f'*{spelling}'


def _shape(schema: ContractSchema) -> list[str]:
    """The Shape interface, its base, one struct per kind, and the dispatcher."""
    found, delegated = schema.shape_projection()

    # `type` is left off the base and declared on each variant: it is what the
    # union discriminates on, and it is what `ShapeKind` returns.
    base = [_row(schema, 'ShapeBase', name, optional=False) for name in found.required if name != 'type']
    # A delegate's keys are optional whatever it says of them: whether it runs at
    # all is the caller's condition, not the delegate's. JSON-schema fields are
    # the exception: they belong only to JsonShape below.
    base += [_row(schema, 'ShapeBase', name, optional=True) for name in found.optional if name not in JSON_ONLY]

    by_model = schema.kinds_by_model()

    # No `Shape` interface here: it holds nothing derived, so it is hand-written
    # in `static/tree.go` beside the two functions that read it.
    blocks = [
        _struct(
            'ShapeBase',
            'ShapeBase holds the fields shared by every expanded type. Kind-specific\n'
            '// facets live on the structs below, which embed this one. `type` is not\n'
            '// here: it is the discriminator, so each variant declares its own.\n'
            '//\n'
            '// Numeric bounds are ExactDecimal; counts such as MaxItems stay numbers\n'
            '// because they are bounded by memory rather than by numeric precision.',
            base,
        ),
    ]

    for model, names in by_model.items():
        rows = [('', 'ShapeBase', '', ''), _field('type', 'ShapeType', optional=False)]
        for facet in schema.shape_facets[model]:
            note = ''
            if facet.wire_form == 'exact_decimal':
                note = '// exact decimal, e.g. "0.01" or "1.7976931348623157E+308"'
            spelling = _spelling(schema.facet_structure(facet))
            rows.append(_field(facet.name, spelling, optional=True, note=note))
        if model == 'JsonShape':
            rows += [_row(schema, 'ShapeBase', name, optional=True) for name in delegated if name in JSON_ONLY]
        spelled = ' or '.join(f'`{name}`' for name in names)
        blocks.append(_struct(model, f'{model} is the expanded form of a type whose kind is {spelled}.', rows))
        blocks.append(
            f'// ShapeKind implements Shape.\nfunc (s *{model}) ShapeKind() ShapeType {{\n\treturn s.Type\n}}'
        )

    blocks.append(_recursion(schema))
    blocks.append(_dispatcher(by_model))
    return blocks


def _recursion(schema: ContractSchema) -> str:
    """The recursion marker, as a shape rather than a record of its own.

    P9 builds a `RecursiveShape` and `shape()` projects it down the generic
    path, so a marker carries `id`, `name` and whatever `ShapeBase` fields the
    type it stands for had. It embeds `ShapeBase` for that reason, and it gets
    no `ShapeKind` method: it is not a `Shape`, because `Shape` is what a
    declaration and a `projection` hold and a marker is neither. `ShapeNode`
    holds it as its own arm.

    Hand-declared. `schema.py` derives records by reading a `_Projector`
    method's AST, and `_Projector.recursion()` is a literal three-key dict that
    never runs, so generating from it declares three keys where seven ship
    (docs/16 § 6.1). `head` is hand-declared for a second reason:
    `shape()` writes it through a loop over `_BACK_POINTERS`, which no AST read
    resolves.
    """
    rows = [
        ('', 'ShapeBase', '', ''),
        ('Type', 'string', '`json:"type"`', '// always RecursionType'),
        ('Head', _spelling(schema.structure_of('ShapeBase', 'head')), '`json:"head"`', ''),
    ]
    return _struct(
        'Recursion',
        'Recursion is a type that repeats here. Do not expand it; look Head up\n'
        '// instead. The kind is in `type` rather than a key of its own, so a\n'
        '// consumer that switches on it and has not handled the value fails loudly.',
        rows,
    )


def _dispatcher(by_model: dict[str, list[str]]) -> str:
    """The kind table, as data.

    Only the table is emitted. `Shape`, `UnmarshalShape` and `shapeOf` read it
    and are hand-written in `static/tree.go`, because none of the three holds
    anything derived.
    """
    rows = (
        (f'ShapeType{_go_name(name)}:', f'func() Shape {{ return &{model}{{}} }},', '', '')
        for model, names in by_model.items()
        for name in names
    )
    return (
        '// shapeKinds is every discriminator the parser declares, mapped to the\n'
        '// variant that implements it. Two keys share a variant exactly where the\n'
        '// parser shares an implementation.\n'
        'var shapeKinds = map[ShapeType]func() Shape{\n' + '\n'.join(_aligned(rows)) + '\n}'
    )


def _struct(name: str, doc: str, rows: Iterable[tuple[str, str, str, str]]) -> str:
    """One struct, its fields aligned the way `gofmt` aligns them."""
    rows = list(rows)
    seen: dict[str, str] = {}
    for field, _, tag, _note in rows:
        if not field:
            continue
        wire = tag.split('"')[1].split(',')[0] if tag else field
        if field in seen:
            raise ValueError(f'{name}: `{seen[field]}` and `{wire}` both spell the Go field `{field}`')
        seen[field] = wire
    return f'// {doc}\ntype {name} struct {{\n' + '\n'.join(_aligned(rows)) + '\n}'


def _aligned(rows: Iterable[tuple[str, str, str, str]]) -> list[str]:
    """Pad columns to a common width, as `gofmt` does: tab indent, space align.

    An embedded field is a row whose name is empty, and it takes part in no
    column: `gofmt` leaves it flush and so does this.
    """
    rows = list(rows)
    widths = [max((len(row[column]) for row in rows if row[0]), default=0) for column in range(3)]
    lines = []
    for field, spelling, tag, note in rows:
        if not field:
            lines.append(f'\t{spelling}')
            continue
        cells = [field.ljust(widths[0]), spelling.ljust(widths[1])]
        cells.append(tag.ljust(widths[2]) if note else tag)
        lines.append(('\t' + ' '.join(cells) + (f' {note}' if note else '')).rstrip())
    return lines


def _go_name(text: str) -> str:
    """A Go identifier for a wire name, in Go's spelling of its acronyms.

    Two inputs arrive: a snake-cased key (`display_name`, `base_uri`) and a
    vocabulary member already spelled by whoever declared it
    (`DocumentationItem`, `API`, `OAuth 1.0`). A word carrying a capital is left
    as written; anything else is title-cased, and an acronym is upper-cased
    whichever way it arrived.
    """
    out = []
    for word in _WORDS.split(text):
        if not word:
            continue
        if word.upper() in _INITIALISMS:
            out.append(word.upper())
        elif word[:1].isupper():
            out.append(word)
        else:
            out.append(word.capitalize())
    return ''.join(out)


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m fastraml.views.bindings golang')
    parser.add_argument('-o', '--output', required=True, help='output file, or - for stdout')
    parser.add_argument('-p', '--package', default='tree', help='name of the generated package (default: tree)')
    parser.add_argument('--conform', help='where to write the conformance driver')
    parser.add_argument(
        '--conform-import', default='conformance/tree', help='import path the driver reaches the package by'
    )
    parser.add_argument('--runtime', help='where to write the reading half; a second file in the same package')
    options = parser.parse_args(arguments)
    write_rendered(options.output, golang(options.package))
    if options.runtime:
        write_rendered(options.runtime, golang_runtime(options.package))
    if options.conform:
        write_rendered(options.conform, golang_conform(options.conform_import))


if __name__ == '__main__':
    main()
