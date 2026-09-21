"""The tree contract, as Go declarations — docs/16-graph.md § 11.11b.

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
  library, and the reason is in `docs/16` § 11.11b.
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
import sys
from typing import TYPE_CHECKING, Final

from .schema import ContractSchema, Emitted, Facet, contract_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ['golang']

#: How neutral Python annotations are represented in Go. These are the *model's*
#: annotations, not the wire's: a `ScalarFacet[int]` arrives as a bare number and
#: a `Fraction` as an exact decimal string (§ 11.4a). Every facet is optional, so
#: the scalars here are bare and `_optional` makes them pointers.
_GO_OF: Final = {
    'ScalarFacet[int] | None': 'int',
    'ScalarFacet[str] | None': 'string',
    'ScalarFacet[bool] | None': 'bool',
    # A `Fraction` is stringified rather than divided: a facet's value is built
    # from the raw scalar text, and `float64` would lose it (CLAUDE.md).
    'ScalarFacet[Fraction] | None': 'ExactDecimal',
    # The pattern's source text, not a compiled object.
    'ScalarFacet[re.Pattern[str]] | None': 'string',
    'list[ScalarFacet[str]] | None': '[]string',
    'BaseShape | None': 'ShapeNode',
    'list[BaseShape] | None': '[]ShapeNode',
    'dict[str, Property] | None': 'PropertiesByName',
    'dict[str, PatternProperty] | None': 'PatternPropertiesByPattern',
    # `discriminator_value` is user data, so it is whatever the author wrote.
    'DataNode | None': 'Json',
}

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

#: Which struct each `_Projector` method produces. The generator checks that
#: every method emitting literal keys is named here, so a new one cannot be
#: forgotten.
_PRODUCES: Final = {
    'model': 'Document',
    'fragment': 'EntryPoint',
    'scheme': 'SecurityScheme',
    'described': 'DescribedBy',
    'endpoint': 'Endpoint',
    'operation': 'Operation',
    'request': 'Operation',
    'response': 'Response',
    'schemes': 'SecuredBy',
    'applied': 'Applied',
    'annotation': 'DocumentAnnotation',
    'example': 'Example',
}

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

#: The type of every structural key. Only the types: which keys exist is read
#: from `tree.py`, and generation fails on a key absent from here.
_STRUCTURAL: Final[dict[str, dict[str, str]]] = {
    'Document': {
        # Go has no literal type. The three constants below the preamble's
        # aliases carry the values these keys always hold.
        'format': 'string',
        'format_version': 'int',
        'view': 'string',
        'base': 'Address',
        'entry_point': '*EntryPoint',
        'types': 'ShapeDeclarationsByFile',
        'annotation_types': 'ShapeDeclarationsByFile',
        'security_schemes': 'SecuritySchemeDeclarationsByFile',
        'endpoints': 'EndpointsByPath',
        'annotations': '[]DocumentAnnotation',
    },
    'EntryPoint': {
        'kind': 'FragmentKind',
        'title': 'string',
        'version': 'string',
        'base_uri': 'string',
        'media_types': '[]string',
        'protocols': '[]Protocol',
        'usage': 'string',
        'description': 'string',
        'base_uri_parameters': 'ParametersByName',
        'documentation': '[]DocumentationItem',
        #: `securedBy:` at the root. Every endpoint declaring none carries the
        #: same list, so this says the API declared a *default*, not what any
        #: one endpoint requires.
        'secured_by': '[]SecuredBy',
        'annotations': '[]Applied',
    },
    'SecurityScheme': {
        'id': '*Address',
        'name': 'string',
        'type': 'SecuritySchemeType',
        'display_name': 'string',
        'description': 'string',
        'settings': 'SecuritySettings',
        'described_by': '*DescribedBy',
        'annotations': '[]Applied',
    },
    'DescribedBy': {
        'headers': 'ParametersByName',
        'query_parameters': 'ParametersByName',
        'query_string': '*ShapeNode',
        'responses': 'ResponsesByStatus',
    },
    'Endpoint': {
        'id': '*Address',
        'operations': 'OperationsByMethod',
        'secured_by': '[]SecuredBy',
        'display_name': 'string',
        'description': 'string',
        'uri_parameters': 'ParametersByName',
        'annotations': '[]Applied',
    },
    'Operation': {
        'id': '*Address',
        'responses': 'ResponsesByStatus',
        'description': 'string',
        'display_name': 'string',
        'protocols': '[]Protocol',
        'secured_by': '[]SecuredBy',
        'annotations': '[]Applied',
        'headers': 'ParametersByName',
        'query_parameters': 'ParametersByName',
        'query_string': '*ShapeNode',
        'bodies': 'BodiesByMediaType',
    },
    'Response': {
        'description': 'string',
        'headers': 'ParametersByName',
        'bodies': 'BodiesByMediaType',
        'annotations': '[]Applied',
    },
    'SecuredBy': {
        'name': 'string',
        'is_null': 'bool',
        'bound': 'bool',
        'declaration': '*Address',
        'scopes': '[]string',
    },
    'Applied': {
        'name': 'string',
        'type': '*Address',
        'value': 'Json',
    },
    'DocumentAnnotation': {
        'name': 'string',
        'target': 'AnnotationTarget',
        'type': '*Address',
        'value': 'Json',
    },
    'Example': {
        'value': 'Json',
        'display_name': 'string',
        'description': 'string',
        'strict': 'bool',
        'annotations': '[]Applied',
    },
}

#: The base fields `shape()` writes itself, before a kind's facets are inlined.
_SHAPE_FIELDS: Final = {
    'id': '*Address',
    'name': '*string',
    'type': 'ShapeType',
    'display_name': 'string',
    'description': 'string',
    'required': 'bool',
    'default': 'Json',
    'example': '*Example',
    'examples': 'ExamplesByName',
    'enum': '[]Json',
    'xml': 'Json',
    'allowed_targets': '[]AnnotationTarget',
    'inherits': '[]ShapeNode',
    #: Custom facet *values* -- what this type supplies. `declared_facets` is
    #: the other half: what a subtype must supply (docs/10 § 4).
    'custom_facets': 'FacetValuesByName',
    'declared_facets': 'PropertiesByName',
    'annotations': '[]Applied',
    'type_expr': 'string',
    #: Both only on a `json` shape, and both about the same schema: the schema
    #: itself with every reference out of it resolved, and the nearest RAML
    #: shape to it (docs/10 § 6.3). `projection` is always an expanded shape and
    #: never a link, but Go cannot narrow a union, so it is a `ShapeNode`.
    'json_schema': 'Json',
    'projection': '*ShapeNode',
    #: A recursion marker. A *shape*, not a record of its own -- see the
    #: TypeScript backend's note and docs/16 § 11.11c. `head` is hand-declared
    #: because `shape()` writes it through a loop over `_BACK_POINTERS`, so no
    #: AST read can see the key.
    'head': 'Ref',
}

#: Fields that only a `json` shape carries. Named once, read twice.
_JSON_ONLY: Final = frozenset({'json_schema', 'projection'})

#: The hand-written half of the binding, verbatim. It is a `.go` file because
#: nothing in it varies with the schema: `gofmt` checks it, an editor highlights
#: it, and a syntax error fails where it was written rather than three layers
#: downstream. Read the way `config.py` reads `config.raml`.
_STATIC: Final = pathlib.Path(__file__).parent / 'static' / 'tree.go'

#: The package clause the static half is written with, so the file is valid Go
#: rather than Go with a hole in it. A `{placeholder}` would cost exactly the
#: thing the file is there for.
_STATIC_PACKAGE: Final = 'package tree'

#: What a Go identifier may not be split across. Anything else separates words.
_WORDS: Final = re.compile(r'[^0-9A-Za-z]+')


# -- generating ----------------------------------------------------------------


def golang(package: str = 'tree') -> str:
    """The whole declaration file: the hand-written half, then the derived one."""
    schema = contract_schema()
    blocks = [
        _static(package),
        _vocabularies(schema),
        *_shape(schema),
        *_structs(schema.projector),
    ]
    return '\n\n'.join(blocks) + '\n'


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


def _structs(emitted: dict[str, Emitted]) -> list[str]:
    """One struct per structural producer, keys checked against the source."""
    written: dict[str, list[tuple[str, str, str, str]]] = {}
    for method, name in _PRODUCES.items():
        found = emitted.get(method)
        if found is None:
            raise LookupError(f'_PRODUCES names `{method}`, which is not a _Projector method')
        declared = _STRUCTURAL.get(name)
        if declared is None:
            raise LookupError(f'no types declared for `{name}` (see _STRUCTURAL)')
        rows = written.setdefault(name, [])
        for key in found.required:
            rows.append(_row(name, declared, key, optional=False))
        for key in found.optional:
            rows.append(_row(name, declared, key, optional=True))

    return [_struct(name, _DOC.get(name, f'{name} is one node of the tree.'), rows) for name, rows in written.items()]


def _row(owner: str, declared: dict[str, str], key: str, *, optional: bool) -> tuple[str, str, str, str]:
    spelling = declared.get(key)
    if spelling is None:
        raise LookupError(f'{owner}.{key}: emitted by tree.py and not declared in _STRUCTURAL. Add its Go type there.')
    return _field(key, spelling, optional=optional)


def _field(key: str, spelling: str, *, optional: bool, note: str = '') -> tuple[str, str, str, str]:
    # `omitzero` and not `omitempty`: the two differ on an empty list, which the
    # tree does emit -- `annotations`, `media_types`, `protocols` and
    # `secured_by` arrive as `[]` over the corpus. `omitempty` would write each
    # of them back as an absent key (docs/16 § 11.11b).
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
    found = schema.projector.get('shape')
    if found is None:
        raise LookupError('_Projector.shape not found')
    # Including what it delegates to, for the reason the TypeScript backend
    # gives: reading only `shape()` means a key added to a delegate reaches the
    # tree and never reaches this file, silently.
    delegated = schema.delegated_fields(found)
    known = set(found.required) | set(found.optional) | set(delegated)
    undeclared = sorted(known - set(_SHAPE_FIELDS))
    if undeclared:
        raise LookupError(f'shape() emits {undeclared}, not declared in _SHAPE_FIELDS')

    # `type` is left off the base and declared on each variant: it is what the
    # union discriminates on, and it is what `ShapeKind` returns.
    base = [_field(name, _SHAPE_FIELDS[name], optional=False) for name in found.required if name != 'type']
    # A delegate's keys are optional whatever it says of them: whether it runs at
    # all is the caller's condition, not the delegate's. JSON-schema fields are
    # the exception: they belong only to JsonShape below.
    base += [_field(name, _SHAPE_FIELDS[name], optional=True) for name in found.optional if name not in _JSON_ONLY]

    by_model: dict[str, list[str]] = {}
    for kind in schema.shape_kinds:
        by_model.setdefault(kind.model, []).append(kind.name)

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
            rows.append(_field(facet.name, _facet_type(model, facet), optional=True, note=note))
        if model == 'JsonShape':
            rows += [_field(name, _SHAPE_FIELDS[name], optional=True) for name in delegated if name in _JSON_ONLY]
        spelled = ' or '.join(f'`{name}`' for name in names)
        blocks.append(_struct(model, f'{model} is the expanded form of a type whose kind is {spelled}.', rows))
        blocks.append(
            f'// ShapeKind implements Shape.\nfunc (s *{model}) ShapeKind() ShapeType {{\n\treturn s.Type\n}}'
        )

    blocks.append(_recursion())
    blocks.append(_dispatcher(by_model))
    return blocks


def _recursion() -> str:
    """The recursion marker, as a shape rather than a record of its own.

    P9 builds a `RecursiveShape` and `shape()` projects it down the generic
    path, so a marker carries `id`, `name` and whatever `ShapeBase` fields the
    type it stands for had. It embeds `ShapeBase` for that reason, and it gets
    no `ShapeKind` method: it is not a `Shape`, because `Shape` is what a
    declaration and a `projection` hold and a marker is neither. `ShapeNode`
    holds it as its own arm.

    Hand-declared because neither half is derivable. `_Projector.recursion()`
    emits the right keys but never runs (docs/16 section 11.11c), and `shape()`
    writes `head` through a loop over `_BACK_POINTERS`, which no AST read
    resolves.
    """
    rows = [
        ('', 'ShapeBase', '', ''),
        ('Type', 'string', '`json:"type"`', '// always RecursionType'),
        ('Head', _SHAPE_FIELDS['head'], '`json:"head"`', ''),
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


def _facet_type(model: str, facet: Facet) -> str:
    if facet.wire_form == 'exact_decimal':
        return 'ExactDecimal'
    if facet.wire_form == 'reference':
        return 'Ref'
    spelling = _GO_OF.get(facet.annotation)
    if spelling is None:
        raise LookupError(f'{model}.{facet.name}: no Go spelling declared for {facet.annotation!r} (see _GO_OF)')
    return spelling


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
    options = parser.parse_args(arguments)
    rendered = golang(options.package)
    if options.output == '-':
        sys.stdout.write(rendered)
        return
    path = pathlib.Path(options.output)
    path.write_text(rendered, encoding='utf-8')
    sys.stdout.write(f'wrote {path.resolve()}\n')


if __name__ == '__main__':
    main()
