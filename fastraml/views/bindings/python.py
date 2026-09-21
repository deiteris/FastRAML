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
here is the same two jobs the TypeScript backend has: map the schema's
annotations and wire forms into a type system, and declare the type of each
*structural* key, which no source analysis can infer from `out['operations']`.
Key sets come from the schema, so a key added to the projection and not declared
here fails generation by name.

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
import pathlib
import re
import sys
from typing import TYPE_CHECKING, Final

from .schema import ContractSchema, Emitted, Facet, contract_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ['python']

#: How neutral Python annotations are represented on the wire. These are the
#: *model's* annotations, not the wire's: a `ScalarFacet[int]` arrives as a bare
#: number, and a `Fraction` as an exact decimal string (§ 11.4a). Wire forms that
#: override an annotation are selected from the schema before this is consulted.
_PYTHON_OF: Final = {
    'ScalarFacet[int] | None': 'int',
    'ScalarFacet[str] | None': 'str',
    'ScalarFacet[bool] | None': 'bool',
    # A `Fraction` is stringified rather than divided: a facet's value is built
    # from the raw scalar text, and `float` would lose it (CLAUDE.md).
    'ScalarFacet[Fraction] | None': 'ExactDecimal',
    # The pattern's source text, not a compiled object.
    'ScalarFacet[re.Pattern[str]] | None': 'str',
    'list[ScalarFacet[str]] | None': 'list[str]',
    'BaseShape | None': 'ShapeNode',
    'list[BaseShape] | None': 'list[ShapeNode]',
    'dict[str, Property] | None': 'dict[str, Property]',
    'dict[str, PatternProperty] | None': 'dict[str, PatternProperty]',
    # `discriminator_value` is user data, so it is whatever the author wrote.
    'DataNode | None': 'Json',
}


# -- the hand-declared half ----------------------------------------------------

#: Which TypedDict each `_Projector` method produces. The generator checks that
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

#: The type of every structural key. Only the types: which keys exist is read
#: from `tree.py`, and generation fails on a key absent from here.
_STRUCTURAL: Final[dict[str, dict[str, str]]] = {
    'Document': {
        'format': "Literal['fastraml-tree']",
        'format_version': 'Literal[1]',
        'view': "Literal['effective']",
        'base': 'Address',
        'entry_point': 'EntryPoint | None',
        'types': 'ShapeDeclarationsByFile',
        'annotation_types': 'ShapeDeclarationsByFile',
        'security_schemes': 'SecuritySchemeDeclarationsByFile',
        'endpoints': 'EndpointsByPath',
        'annotations': 'list[DocumentAnnotation]',
    },
    'EntryPoint': {
        'kind': 'FragmentKind',
        'title': 'str',
        'version': 'str',
        'base_uri': 'str',
        'media_types': 'list[str]',
        'protocols': 'list[Protocol]',
        'usage': 'str',
        'description': 'str',
        'base_uri_parameters': 'dict[str, Parameter]',
        'documentation': 'list[DocumentationItem]',
        #: `securedBy:` at the root. Every endpoint declaring none carries the
        #: same list, so this says the API declared a *default*, not what any
        #: one endpoint requires.
        'secured_by': 'list[SecuredBy]',
        'annotations': 'list[Applied]',
    },
    'SecurityScheme': {
        'id': 'Address | None',
        'name': 'str',
        'type': 'SecuritySchemeType',
        'display_name': 'str',
        'description': 'str',
        'settings': 'SecuritySettings',
        'described_by': 'DescribedBy',
        'annotations': 'list[Applied]',
    },
    'DescribedBy': {
        'headers': 'dict[str, Parameter]',
        'query_parameters': 'dict[str, Parameter]',
        'query_string': 'ShapeNode | None',
        'responses': 'ResponsesByStatus',
    },
    'Endpoint': {
        'id': 'Address | None',
        'operations': 'OperationsByMethod',
        'secured_by': 'list[SecuredBy]',
        'display_name': 'str',
        'description': 'str',
        'uri_parameters': 'dict[str, Parameter]',
        'annotations': 'list[Applied]',
    },
    'Operation': {
        'id': 'Address | None',
        'responses': 'ResponsesByStatus',
        'description': 'str',
        'display_name': 'str',
        'protocols': 'list[Protocol]',
        'secured_by': 'list[SecuredBy]',
        'annotations': 'list[Applied]',
        'headers': 'dict[str, Parameter]',
        'query_parameters': 'dict[str, Parameter]',
        'query_string': 'ShapeNode | None',
        'bodies': 'BodiesByMediaType',
    },
    'Response': {
        'description': 'str',
        'headers': 'dict[str, Parameter]',
        'bodies': 'BodiesByMediaType',
        'annotations': 'list[Applied]',
    },
    'SecuredBy': {
        'name': 'str',
        'is_null': 'bool',
        'bound': 'bool',
        'declaration': 'Address | None',
        'scopes': 'list[str] | None',
    },
    'Applied': {
        'name': 'str',
        'type': 'Address | None',
        'value': 'Json',
    },
    'DocumentAnnotation': {
        'name': 'str',
        'target': 'AnnotationTarget',
        'type': 'Address | None',
        'value': 'Json',
    },
    'Example': {
        'value': 'Json',
        'display_name': 'str',
        'description': 'str',
        'strict': 'bool',
        'annotations': 'list[Applied]',
    },
}

#: The base fields `shape()` writes itself, before a kind's facets are inlined.
_SHAPE_FIELDS: Final = {
    'id': 'Address | None',
    'name': 'str | None',
    'type': 'ShapeType',
    'display_name': 'str',
    'description': 'str',
    'required': 'bool',
    'default': 'Json',
    'example': 'Example',
    'examples': 'dict[str, Example]',
    'enum': 'list[Json]',
    'xml': 'Json',
    'allowed_targets': 'list[AnnotationTarget]',
    'inherits': 'list[ShapeNode]',
    #: Custom facet *values* -- what this type supplies. `declared_facets` is
    #: the other half: what a subtype must supply (docs/10 § 4).
    'custom_facets': 'dict[str, Json]',
    'declared_facets': 'dict[str, Property]',
    'annotations': 'list[Applied]',
    'type_expr': 'str',
    #: Both only on a `json` shape, and both about the same schema: the schema
    #: itself with every reference out of it resolved, and the nearest RAML
    #: shape to it (docs/10 § 6.3).
    'json_schema': 'Json',
    'projection': 'Shape',
    #: A recursion marker. A *shape*, not a record of its own -- see the
    #: TypeScript backend's note and docs/16 § 11.11c. `head` is hand-declared
    #: because `shape()` writes it through a loop over `_BACK_POINTERS`, so no
    #: AST read can see the key.
    'head': 'Ref',
}

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

    for block in (*_shape(schema, behind), *_typed_dicts(schema.projector, behind)):
        blocks.append(block)
        behind.add(_declared_in(block))

    blocks.append(_exports(schema))
    return '\n\n\n'.join(blocks) + '\n'


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


def _typed_dicts(emitted: dict[str, Emitted], behind: _Behind) -> list[str]:
    """One TypedDict per structural producer, keys checked against the source.

    Every one of them is emitted against the same `behind`: they reference each
    other, so declaring the earlier ones first would only move which are quoted.
    """
    written: dict[str, list[str]] = {}
    for method, name in _PRODUCES.items():
        found = emitted.get(method)
        if found is None:
            raise LookupError(f'_PRODUCES names `{method}`, which is not a _Projector method')
        declared = _STRUCTURAL.get(name)
        if declared is None:
            raise LookupError(f'no types declared for `{name}` (see _STRUCTURAL)')
        lines = written.setdefault(name, [])
        for key in found.required:
            lines.append(_field(name, declared, key, behind, optional=False))
        for key in found.optional:
            lines.append(_field(name, declared, key, behind, optional=True))

    return [f'class {name}(TypedDict):\n' + '\n'.join(dict.fromkeys(lines)) for name, lines in written.items()]


def _field(owner: str, declared: dict[str, str], name: str, behind: _Behind, *, optional: bool) -> str:
    spelling = declared.get(name)
    if spelling is None:
        raise LookupError(
            f'{owner}.{name}: emitted by tree.py and not declared in _STRUCTURAL. Add its Python type there.'
        )
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

    # `type` is left off the base and declared on each variant: a TypedDict
    # subclass may not re-declare a key, so this is the only way the
    # discriminator narrows.
    base = [
        f'    {name}: {_annotation(_SHAPE_FIELDS[name], behind, optional=False)}'
        for name in found.required
        if name != 'type'
    ]
    # A delegate's keys are optional whatever it says of them: whether it runs
    # at all is the caller's condition, not the delegate's. JSON-schema fields
    # are the exception: they belong only to JsonShape below.
    base += [
        f'    {name}: {_annotation(_SHAPE_FIELDS[name], behind, optional=True)}'
        for name in found.optional
        if name not in {'json_schema', 'projection'}
    ]

    by_model: dict[str, list[str]] = {}
    for kind in schema.shape_kinds:
        by_model.setdefault(kind.model, []).append(kind.name)

    variants: list[str] = []
    for model, names in by_model.items():
        lines = [f'    type: Literal[{", ".join(repr(name) for name in names)}]']
        for facet in schema.shape_facets[model]:
            note = ''
            if facet.wire_form == 'exact_decimal':
                note = "  # exact decimal, e.g. '0.01' or '1.7976931348623157E+308'"
            lines.append(f'    {facet.name}: {_annotation(_facet_type(model, facet), behind, optional=True)}{note}')
        if model == 'JsonShape':
            lines.extend(
                f'    {name}: {_annotation(_SHAPE_FIELDS[name], behind, optional=True)}'
                for name in delegated
                if name in {'json_schema', 'projection'}
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
    return [head, *variants, _union_alias('Shape', tuple(by_model)), _recursion()]


def _recursion() -> str:
    """The recursion marker, as a shape rather than a record of its own.

    P9 builds a `RecursiveShape` and `shape()` projects it down the generic
    path, so a marker carries `id`, `name` and whatever `ShapeBase` fields the
    type it stands for had. It is not a member of `Shape`: `Shape` is what a
    declaration and a `projection` hold, and a marker is neither.

    Hand-declared because neither half is derivable. `_Projector.recursion()`
    emits the right keys but never runs (docs/16 section 11.11c), and `shape()`
    writes `head` through a loop over `_BACK_POINTERS`, which no AST read
    resolves. `name` is not re-declared: a TypedDict subclass may not, and
    `ShapeBase` already carries it.
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
        f'    head: {_SHAPE_FIELDS["head"]}'
    )


def _union_alias(name: str, members: tuple[str, ...]) -> str:
    single = f'{name}: TypeAlias = {" | ".join(members)}'
    if len(single) <= _LINE_LENGTH:
        return single
    body = '\n'.join(f'    {member}' if index == 0 else f'    | {member}' for index, member in enumerate(members))
    return f'{name}: TypeAlias = (\n{body}\n)'


def _facet_type(model: str, facet: Facet) -> str:
    if facet.wire_form == 'exact_decimal':
        return 'ExactDecimal'
    if facet.wire_form == 'reference':
        return 'Ref'
    spelling = _PYTHON_OF.get(facet.annotation)
    if spelling is None:
        raise LookupError(
            f'{model}.{facet.name}: no Python spelling declared for {facet.annotation!r} (see _PYTHON_OF)'
        )
    return spelling


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
        *dict.fromkeys(_PRODUCES.values()),
        *dict.fromkeys(kind.model for kind in schema.shape_kinds),
    ]
    body = '\n'.join(f'    {name!r},' for name in sorted(set(names)))
    return f'__all__ = [\n{body}\n]'


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m fastraml.views.bindings python')
    parser.add_argument('-o', '--output', required=True, help='output file, or - for stdout')
    options = parser.parse_args(arguments)
    rendered = python()
    if options.output == '-':
        sys.stdout.write(rendered)
        return
    path = pathlib.Path(options.output)
    path.write_text(rendered, encoding='utf-8')
    sys.stdout.write(f'wrote {path.resolve()}\n')


if __name__ == '__main__':
    main()
