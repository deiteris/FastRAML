"""The tree contract, as TypeScript declarations — docs/16-graph.md § 11.11.

A consumer of `fastraml tree` has to know what the JSON holds. Written by hand
that list goes stale the first time a facet is added to a kind, and it goes
stale *quietly*: a key the declarations omit still arrives, and a consumer that
does not read it looks exactly like a document that did not say it. That is the
argument `facet_slots` makes for one facet vocabulary (docs/14 § 4, law 14), and
it applies with more force across a language boundary, where no type checker
spans both sides.

The language-neutral schema derives the contract from two sources, by reading
source rather than importing it:

* **Which keys the projection emits** — from `tree.py`'s own AST. Every
  `_Projector` method builds a dict, and the keys are literals: in the opening
  display, in `out[...] = ...`, or in the `for field in (...)` loops. Required
  and optional fall out of the same read, since a key assigned under an `if` is
  one the projection may omit.
* **What a kind's facets are, and their types** — from the `self.x: T = None`
  annotations in each kind's `__init__`. Python discards those at runtime, so
  this reads them from the AST; the alternative is a table per kind, which is
  the thing being avoided.

This backend contains no Python source analysis. It maps the neutral schema's
annotations and wire forms into TypeScript and supplies the value type of each
structural key: `out['operations']` holds an expression, and no source analysis
can infer `OperationsByMethod` from it. The key sets still come from the schema,
so **a key added to the projection and not declared here fails generation by
name**.

`python -m fastraml.views.bindings typescript -o FILE` selects this backend and
writes a destination chosen by its caller. `tests/unit/test_bindings.py` asserts
the viewer's checked-in copy is what generation produces.
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
    'recursion': 'Recursion',
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
    'Recursion': {
        'type': "'recursive'",
        'name': 'string | null',
        'head': 'Ref',
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
}

_PREAMBLE: Final = """\
/**
 * The `fastraml tree` contract.
 *
 * GENERATED by `python -m fastraml.views.bindings typescript` -- do not edit. The key sets
 * are read from `fastraml/views/tree.py` and the facets from the kind classes in
 * `fastraml/types/`, so a facet added to a kind arrives here without this file
 * being touched. `tests/unit/test_bindings.py` fails when the two disagree.
 *
 * The metamodel is three constructs (docs/16-graph.md section 11.10):
 *
 *   {"$ref": <address>}                            a link -- look the target up
 *   {"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
 *   anything else                                  containment -- descend
 *
 * A consumer descends containment, follows a link when it chooses to, and stops
 * at a recursion marker. It maintains no ancestor set. Requires
 * `ParseOptions(unwrap=True)`, which `fastraml tree` uses.
 */

/** A structural address: stable across re-parses, and the identity of a node. */
export type Address = string;

/** An exact decimal carried as text so no consumer rounds it through a double. */
export type ExactDecimal = string;

export interface JsonObject {
  [key: string]: Json;
}

export type Json = string | number | boolean | null | Json[] | JsonObject;

export type Protocol = 'HTTP' | 'HTTPS';
export type ParameterBinding = 'uri' | 'query' | 'header';
export type SecuritySchemeType =
  | 'null'
  | 'OAuth 1.0'
  | 'OAuth 2.0'
  | 'Basic Authentication'
  | 'Digest Authentication'
  | 'Pass Through'
  | `x-${string}`;

export type SourceFile = string;
export type DeclarationName = string;
export type EndpointPath = string;
export type StatusCode = string;
export type MediaType = string;

/** A link. Its sole key is the test -- an expanded shape carries `id` as well. */
export interface Ref {
  $ref: Address;
}

export type ShapeNode = Shape | Ref | Recursion;
export type ShapeDeclarations = Record<DeclarationName, Shape | Ref>;
export type ShapeDeclarationsByFile = Record<SourceFile, ShapeDeclarations>;
export type SecuritySchemeDeclarations = Record<DeclarationName, SecurityScheme>;
export type SecuritySchemeDeclarationsByFile = Record<SourceFile, SecuritySchemeDeclarations>;
export type EndpointsByPath = Record<EndpointPath, Endpoint>;
export type OperationsByMethod = Partial<Record<HttpMethod, Operation>>;
export type ResponsesByStatus = Record<StatusCode, Response>;
export type BodiesByMediaType = Record<MediaType, ShapeNode | null>;
export type SecuritySetting = string | string[];
export type SecuritySettings = Record<string, SecuritySetting>;
"""


# -- generating ----------------------------------------------------------------


def typescript() -> str:
    """The whole declaration file."""
    schema = contract_schema()
    parts = [_PREAMBLE, _vocabularies(schema), _shape(schema), *_interfaces(schema.projector), _records()]
    return '\n'.join(parts)


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


def _records() -> str:
    """The small fixed records nested inside structural fields."""
    return (
        'export interface DocumentationItem {\n'
        '  title: string;\n'
        '  content: string;\n'
        '}\n\n'
        'export interface Property {\n'
        '  required: boolean;\n'
        '  type: ShapeNode | null;\n'
        '}\n\n'
        'export interface PatternProperty {\n'
        '  pattern: string;\n'
        '  type: ShapeNode | null;\n'
        '}\n\n'
        'export interface Parameter {\n'
        '  binding: ParameterBinding;\n'
        '  required: boolean;\n'
        '  type: ShapeNode | null;\n'
        '}\n'
    )


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
