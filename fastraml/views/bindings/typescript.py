"""The tree contract, as TypeScript declarations — docs/16-graph.md § 11.11.

Run `python -m fastraml.views.bindings typescript -o FILE`. The caller names the
destination; `-o -` writes stdout. `tests/unit/test_bindings.py` asserts that
the viewer's checked-in copy is what generation produces.

A consumer of `fastraml tree` has to know what the JSON holds. A hand-written
list goes stale the first time a facet is added to a kind, and it goes stale
*quietly*: a key the declarations omit still arrives, and a consumer that does
not read it looks exactly like a document that did not say it. That is law 14's
argument (docs/14 § 4), applied across a boundary no type checker spans.

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
import sys
from typing import TYPE_CHECKING, Final

from .schema import ContractSchema, Emitted, Facet, contract_schema

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['typescript']

#: How neutral Python annotations are represented in TypeScript. Wire forms
#: that override an annotation (`ExactDecimal`, a back-reference) are selected
#: from the schema before this table is consulted.
_JSON_OF: Final = {
    'ScalarFacet[int] | None': 'number',
    'ScalarFacet[str] | None': 'string',
    'ScalarFacet[bool] | None': 'boolean',
    # A `Fraction` is stringified rather than divided: a facet's value is built
    # from the raw scalar text, and `float` would lose it (CLAUDE.md).
    'ScalarFacet[Fraction] | None': 'ExactDecimal',
    # The pattern's source text, not a compiled object.
    'ScalarFacet[re.Pattern[str]] | None': 'string',
    'list[ScalarFacet[str]] | None': 'string[]',
    'BaseShape | None': 'ShapeNode',
    'list[BaseShape] | None': 'ShapeNode[]',
    'dict[str, Property] | None': 'Record<string, Property>',
    'dict[str, PatternProperty] | None': 'Record<string, PatternProperty>',
    # `discriminator_value` is user data, so it is whatever the author wrote.
    'DataNode | None': 'Json',
}


# -- the hand-declared half ----------------------------------------------------

#: Which interface each `_Projector` method produces. The generator checks that
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
        'format': "'fastraml-tree'",
        'format_version': '1',
        'view': "'effective'",
        'base': 'Address',
        'entry_point': 'EntryPoint | null',
        'types': 'ShapeDeclarationsByFile',
        'annotation_types': 'ShapeDeclarationsByFile',
        'security_schemes': 'SecuritySchemeDeclarationsByFile',
        'endpoints': 'EndpointsByPath',
        'annotations': 'DocumentAnnotation[]',
    },
    'EntryPoint': {
        'kind': 'FragmentKind',
        'title': 'string',
        'version': 'string',
        'base_uri': 'string',
        'media_types': 'string[]',
        'protocols': 'Protocol[]',
        'usage': 'string',
        'description': 'string',
        'base_uri_parameters': 'Record<string, Parameter>',
        'documentation': 'DocumentationItem[]',
        #: `securedBy:` at the root. Every endpoint declaring none carries the
        #: same list, so this says the API declared a *default*, not what any
        #: one endpoint requires.
        'secured_by': 'SecuredBy[]',
        'annotations': 'Applied[]',
    },
    'SecurityScheme': {
        'id': 'Address | null',
        'name': 'string',
        'type': 'SecuritySchemeType',
        'display_name': 'string',
        'description': 'string',
        'settings': 'SecuritySettings',
        'described_by': 'DescribedBy',
        'annotations': 'Applied[]',
    },
    'DescribedBy': {
        'headers': 'Record<string, Parameter>',
        'query_parameters': 'Record<string, Parameter>',
        'query_string': 'ShapeNode | null',
        'responses': 'ResponsesByStatus',
    },
    'Endpoint': {
        'id': 'Address | null',
        'operations': 'OperationsByMethod',
        'secured_by': 'SecuredBy[]',
        'display_name': 'string',
        'description': 'string',
        'uri_parameters': 'Record<string, Parameter>',
        'annotations': 'Applied[]',
    },
    'Operation': {
        'id': 'Address | null',
        'responses': 'ResponsesByStatus',
        'description': 'string',
        'display_name': 'string',
        'protocols': 'Protocol[]',
        'secured_by': 'SecuredBy[]',
        'annotations': 'Applied[]',
        'headers': 'Record<string, Parameter>',
        'query_parameters': 'Record<string, Parameter>',
        'query_string': 'ShapeNode | null',
        'bodies': 'BodiesByMediaType',
    },
    'Response': {
        'description': 'string',
        'headers': 'Record<string, Parameter>',
        'bodies': 'BodiesByMediaType',
        'annotations': 'Applied[]',
    },
    'SecuredBy': {
        'name': 'string',
        'is_null': 'boolean',
        'bound': 'boolean',
        'declaration': 'Address | null',
        'scopes': 'string[] | null',
    },
    'Applied': {
        'name': 'string',
        'type': 'Address | null',
        'value': 'Json',
    },
    'DocumentAnnotation': {
        'name': 'string',
        'target': 'AnnotationTarget',
        'type': 'Address | null',
        'value': 'Json',
    },
    'Example': {
        'value': 'Json',
        'display_name': 'string',
        'description': 'string',
        'strict': 'boolean',
        'annotations': 'Applied[]',
    },
}

#: The base fields `shape()` writes itself, before a kind's facets are inlined.
_SHAPE_FIELDS: Final = {
    'id': 'Address | null',
    'name': 'string | null',
    'type': 'ShapeType',
    'display_name': 'string',
    'description': 'string',
    'required': 'boolean',
    'default': 'Json',
    'example': 'Example',
    'examples': 'Record<string, Example>',
    'enum': 'Json[]',
    'xml': 'Json',
    'allowed_targets': 'AnnotationTarget[]',
    'inherits': 'ShapeNode[]',
    #: Custom facet *values* -- what this type supplies. `declared_facets` is
    #: the other half: what a subtype must supply (docs/10 § 4).
    'custom_facets': 'Record<string, Json>',
    'declared_facets': 'Record<string, Property>',
    'annotations': 'Applied[]',
    'type_expr': 'string',
    #: Both only on a `json` shape, and both about the same schema: the schema
    #: itself with every reference out of it resolved, and the nearest RAML
    #: shape to it (docs/10 § 6.3).
    'json_schema': 'Json',
    'projection': 'Shape',
    #: A recursion marker. A *shape*, not a record of its own: P9 builds a
    #: `RecursiveShape` and `shape()` projects it down the generic path, so it
    #: carries `id`, `name` and any `ShapeBase` field the type it stands for
    #: had. `_Projector.recursion()` is a safety net that never fires, and the
    #: contract used to be generated from it -- which is how `description`,
    #: `custom_facets`, `annotations` and `id` went undeclared (docs/16
    #: § 11.11c). `head` is hand-declared because `shape()` writes it through a
    #: loop over `_BACK_POINTERS`, so no AST read can see the key.
    'head': 'Ref',
}

#: The hand-written half of the binding. Nothing in it varies with the schema,
#: so it is a declaration file rather than a Python string: an editor reads it,
#: and a syntax error fails where it was written. Read the way `config.py` reads
#: `config.raml`.
_STATIC: Final = pathlib.Path(__file__).parent / 'static' / 'tree.d.ts'


# -- generating ----------------------------------------------------------------


def typescript() -> str:
    """The whole declaration file: the hand-written half, then the derived one."""
    schema = contract_schema()
    parts = [_static(), _vocabularies(schema), _shape(schema), *_interfaces(schema.projector)]
    return '\n'.join(parts)


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


def _interfaces(emitted: dict[str, Emitted]) -> list[str]:
    """One interface per structural producer, keys checked against the source."""
    written: dict[str, list[str]] = {}
    for method, interface in _PRODUCES.items():
        found = emitted.get(method)
        if found is None:
            raise LookupError(f'_PRODUCES names `{method}`, which is not a _Projector method')
        declared = _STRUCTURAL.get(interface)
        if declared is None:
            raise LookupError(f'no types declared for `{interface}` (see _STRUCTURAL)')
        lines = written.setdefault(interface, [])
        for name in found.required:
            lines.append(_field(interface, declared, name, optional=False))
        for name in found.optional:
            lines.append(_field(interface, declared, name, optional=True))

    return [
        f'export interface {interface} {{\n' + '\n'.join(dict.fromkeys(lines)) + '\n}\n'
        for interface, lines in written.items()
    ]


def _field(interface: str, declared: dict[str, str], name: str, *, optional: bool) -> str:
    spelling = declared.get(name)
    if spelling is None:
        raise LookupError(
            f'{interface}.{name}: emitted by tree.py and not declared in _STRUCTURAL. Add its TypeScript type there.'
        )
    return f'  {name}{"?" if optional else ""}: {spelling};'


def _shape(schema: ContractSchema) -> str:
    """A common shape record and one discriminator-derived interface per kind."""
    found = schema.projector.get('shape')
    if found is None:
        raise LookupError('_Projector.shape not found')
    # Including what it delegates to. `shape()` merges two methods' results into
    # its own: `kind_facets`, whose keys are the kinds' facets and are read
    # below, and `json_schema`, whose keys are literal. Reading only `shape()`
    # meant a key added to a delegate reached the tree and never reached this
    # file -- the one failure this generator exists to make impossible, and it
    # was silent, which is worse than the wrong type.
    delegated = schema.delegated_fields(found)
    known = set(found.required) | set(found.optional) | set(delegated)
    undeclared = sorted(known - set(_SHAPE_FIELDS))
    if undeclared:
        raise LookupError(f'shape() emits {undeclared}, not declared in _SHAPE_FIELDS')

    base = [f'  {name}: {_SHAPE_FIELDS[name]};' for name in found.required if name != 'type']
    # A delegate's keys are optional whatever it says of them: whether it runs
    # at all is the caller's condition, not the delegate's. JSON-schema fields
    # are the exception: they belong only to JsonShape below.
    base += [
        f'  {name}?: {_SHAPE_FIELDS[name]};' for name in found.optional if name not in {'json_schema', 'projection'}
    ]

    by_model: dict[str, list[str]] = {}
    for kind in schema.shape_kinds:
        by_model.setdefault(kind.model, []).append(kind.name)

    variants: list[str] = []
    for model, names in by_model.items():
        lines = [f'  type: {" | ".join(repr(name) for name in names)};']
        for facet in schema.shape_facets[model]:
            note = ''
            if facet.wire_form == 'exact_decimal':
                note = ' // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"'
            lines.append(f'  {facet.name}?: {_facet_type(model, facet)};{note}')
        if model == 'JsonShape':
            lines.extend(
                f'  {name}?: {_SHAPE_FIELDS[name]};' for name in delegated if name in {'json_schema', 'projection'}
            )
        variants.append(f'export interface {model} extends ShapeBase {{\n' + '\n'.join(lines) + '\n}\n')

    variants.append(_recursion())

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


def _recursion() -> str:
    """The recursion marker, as a shape rather than a record of its own.

    P9 builds a `RecursiveShape` and `shape()` projects it down the generic
    path, so a marker carries `id`, `name` and whatever `ShapeBase` fields the
    type it stands for had. It is not in `Shape`: `Shape` is what a declaration
    and a `projection` hold, and a marker is neither.

    Hand-declared because neither half is derivable. `_Projector.recursion()`
    emits the right keys but never runs (docs/16 § 11.11c), and `shape()` writes
    `head` through a loop over `_BACK_POINTERS`, which no AST read resolves.
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
        f'  head: {_SHAPE_FIELDS["head"]};\n'
        '}\n'
    )


def _facet_type(model: str, facet: Facet) -> str:
    if facet.wire_form == 'exact_decimal':
        return 'ExactDecimal'
    if facet.wire_form == 'reference':
        return 'Ref'
    spelling = _JSON_OF.get(facet.annotation)
    if spelling is None:
        raise LookupError(
            f'{model}.{facet.name}: no TypeScript spelling declared for {facet.annotation!r} (see _JSON_OF)'
        )
    return spelling


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m fastraml.views.bindings typescript')
    parser.add_argument('-o', '--output', required=True, help='output file, or - for stdout')
    options = parser.parse_args(arguments)
    rendered = typescript()
    if options.output == '-':
        sys.stdout.write(rendered)
        return
    path = pathlib.Path(options.output)
    path.write_text(rendered, encoding='utf-8')
    sys.stdout.write(f'wrote {path.resolve()}\n')


if __name__ == '__main__':
    main()
