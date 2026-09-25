"""Opt-in RAML authoring conventions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from fastraml.parser.uritemplates import extract_uri_template_params
from fastraml.positions import UNKNOWN
from fastraml.types.complex_ import ArrayShape, ObjectShape, UnionShape
from fastraml.types.examples import examples_of
from fastraml.types.expressions.parser import Optional_, Primitive, Union, parse_expression
from fastraml.types.inference import FACET_TYPE_HINT
from fastraml.types.jsonschema_ import JsonShape
from fastraml.views.graph import is_declaration
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity
from fastraml.views.lint.source import declaration_nodes, mapping_value
from fastraml.yamlnode import NodeKind, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import EndPoint, Operation, Response
    from fastraml.parser.fragments import APIFragment
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.types.base import BaseShape, PatternProperty, Property
    from fastraml.views.lint.engine import Context
    from fastraml.yamlnode import Node

__all__ = [
    'AvoidExplicitInferredType',
    'ExplicitUriParameter',
    'MissingDescription',
    'MissingDisplayName',
    'MissingExample',
    'PreferArrayExpression',
    'PreferInlineAlias',
    'PreferOptionalProperty',
    'PreferOptionalType',
    'RequireClosedObject',
    'UnanchoredPatternProperty',
    'UnconstrainedPatternProperty',
    'UniqueItemsDiscouraged',
]


def _source_mapping(ctx: Context, base: BaseShape) -> Node | None:
    if isinstance(base.shape, JsonShape):
        return None
    found = declaration_nodes(ctx.raml, base.id)
    return found[1] if found is not None and found[1].kind is NodeKind.MAPPING else None


class ExplicitUriParameter:
    meta: ClassVar = RuleMeta(
        'explicit-uri-parameter',
        Category.STYLE,
        'declare every URI template parameter',
        'An explicit declaration documents the parameter type and leaves room for constraints, examples and annotations.',
        Severity.WARNING,
        good=('#%RAML 1.0\ntitle: t\n/users/{userId}:\n  uriParameters:\n    userId: string\n'),
        bad='#%RAML 1.0\ntitle: t\n/users/{userId}:\n  get:\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        names = dict.fromkeys(
            expression.name
            for expression in extract_uri_template_params(endpoint.uri, endpoint.location, endpoint.key_pos)
        )
        return (
            ctx.on(
                self.meta,
                'URI template parameter has no explicit declaration',
                endpoint,
                iri=iri,
                parameter=name,
                path=endpoint.full_uri,
            )
            for name in names
            if endpoint.uri_parameters[name].synthesized
        )


class PreferArrayExpression:
    requires_source: ClassVar = True

    meta: ClassVar = RuleMeta(
        'prefer-array-expression',
        Category.STYLE,
        'prefer T[] to array plus items',
        'The type expression keeps the item type and collection together and is shorter without losing meaning.',
        Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  Names: string[]\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  Names:\n    type: array\n    items: string\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if not isinstance(base.shape, ArrayShape) or (node := _source_mapping(ctx, base)) is None:
            return ()
        type_entry, items = mapping_value(node, 'type'), mapping_value(node, 'items')
        if (
            type_entry is None
            or items is None
            or type_entry[1].value != 'array'
            or items[1].kind is not NodeKind.SCALAR
        ):
            return ()
        return (
            ctx.on(
                self.meta,
                'array can use type expression notation',
                base,
                iri=iri,
                position=type_entry[0].position,
                type=base.name or 'anonymous',
            ),
        )


class PreferOptionalProperty:
    meta: ClassVar = RuleMeta(
        'prefer-optional-property',
        Category.STYLE,
        'prefer ? to required false',
        'RAML provides concise optional-property notation on the property name.',
        Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n      name?: string\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n      name:\n        type: string\n        required: false\n',
    )

    def property_(self, ctx: Context, iri: str, prop: Property) -> Iterable[Finding]:
        required = prop.base.required
        if prop.required or required is None or required.value:
            return ()
        return (
            ctx.on(
                self.meta,
                'optional property uses required false',
                prop.base,
                iri=iri,
                position=required.key_pos,
                property=prop.name,
            ),
        )


class PreferOptionalType:
    meta: ClassVar = RuleMeta(
        'prefer-optional-type',
        Category.STYLE,
        'prefer T? to T or nil',
        'The optional operator is RAML shorthand for a two-member union with nil.',
        Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  Maybe: string?\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  Maybe: string | nil\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        expr = base.type_expr
        if (
            not isinstance(base.shape, UnionShape)
            or expr is None
            or expr.kind is not NodeKind.SCALAR
            or '?' in expr.value
        ):
            return ()
        parsed = parse_expression(expr.value, ctx.raml.expr_cache)
        if isinstance(parsed, Optional_) or not isinstance(parsed, Union) or len(parsed.members) != 2:  # noqa: PLR2004
            return ()
        if not any(isinstance(member, Primitive) and member.name in {'nil', 'null'} for member in parsed.members):
            return ()
        return (
            ctx.on(
                self.meta,
                'nil union can use optional notation',
                base,
                iri=iri,
                position=expr.position,
                type=base.name or 'anonymous',
            ),
        )


class AvoidExplicitInferredType:
    requires_source: ClassVar = True

    meta: ClassVar = RuleMeta(
        'avoid-explicit-inferred-type',
        Category.STYLE,
        'omit a type implied by its facets',
        'RAML infers a declaration kind from kind-specific facets, making the explicit built-in type redundant.',
        Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  Name:\n    maxLength: 20\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  Name:\n    type: string\n    maxLength: 20\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        node = _source_mapping(ctx, base)
        if node is None:
            return ()
        type_entry = mapping_value(node, 'type')
        if type_entry is None or type_entry[1].kind is not NodeKind.SCALAR:
            return ()
        hints = {FACET_TYPE_HINT[key.value] for key, _ in pairs(node) if key.value in FACET_TYPE_HINT}
        if hints != {type_entry[1].value}:
            return ()
        return (
            ctx.on(
                self.meta,
                'type is implied by facets',
                base,
                iri=iri,
                position=type_entry[0].position,
                type=base.name or 'anonymous',
            ),
        )


class PreferInlineAlias:
    requires_source: ClassVar = True

    meta: ClassVar = RuleMeta(
        'prefer-inline-alias',
        Category.STYLE,
        'prefer scalar notation for a type-only alias',
        'A mapping containing only type adds no facets and obscures that the declaration is a plain alias.',
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  B: string\n  A: B\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  B: string\n  A:\n    type: B\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        node = _source_mapping(ctx, base)
        if node is None or len(node.content) != 2:  # noqa: PLR2004 - one key/value pair
            return ()
        entry = mapping_value(node, 'type')
        if entry is None or entry[1].kind is not NodeKind.SCALAR:
            return ()
        return (
            ctx.on(
                self.meta,
                'type-only mapping can use inline notation',
                base,
                iri=iri,
                position=entry[0].position,
                type=entry[1].value,
            ),
        )


class UniqueItemsDiscouraged:
    meta: ClassVar = RuleMeta(
        'avoid-unique-items',
        Category.STYLE,
        'avoid uniqueItems on arrays',
        'Semantic uniqueness can require expensive deep comparisons and is difficult for generated clients to enforce.',
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  A: string[]\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  A:\n    type: array\n    uniqueItems: true\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if not isinstance(shape, ArrayShape) or shape.unique_items is None or not shape.unique_items.value:
            return ()
        facet = shape.unique_items
        return (ctx.on(self.meta, 'array requires unique items', facet, iri=iri, type=base.name or 'anonymous'),)


class RequireClosedObject:
    meta: ClassVar = RuleMeta(
        'require-closed-object',
        Category.STYLE,
        'prefer additionalProperties false',
        'Closed objects catch misspelled and undocumented fields instead of accepting them silently.',
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    additionalProperties: false\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  T: object\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if not isinstance(shape, ObjectShape) or (
            shape.additional_properties is not None and not shape.additional_properties.value
        ):
            return ()
        return (
            ctx.on(self.meta, 'object permits additional properties', base, iri=iri, type=base.name or 'anonymous'),
        )


class UnconstrainedPatternProperty:
    meta: ClassVar = RuleMeta(
        'unconstrained-pattern-property',
        Category.STYLE,
        'avoid universal pattern properties',
        'A pattern matching every name documents no useful property-name constraint.',
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n      /^x-.+$/: string\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n      //: string\n',
    )

    def pattern_property(self, ctx: Context, iri: str, prop: PatternProperty) -> Iterable[Finding]:
        if prop.pattern.pattern:
            return ()
        return (
            ctx.on(self.meta, 'pattern property matches every name', prop.base, iri=iri, pattern=prop.pattern.pattern),
        )


class UnanchoredPatternProperty:
    meta: ClassVar = RuleMeta(
        'unanchored-pattern-property',
        Category.STYLE,
        'anchor pattern-property expressions',
        'Pattern properties use search semantics; missing anchors can match unintended property names.',
        Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n      /^x-.+$/: string\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    properties:\n      /x-/: string\n',
    )

    def pattern_property(self, ctx: Context, iri: str, prop: PatternProperty) -> Iterable[Finding]:
        pattern = prop.pattern.pattern
        if pattern.startswith(('^', '\\A')) and pattern.endswith(('$', '\\Z')):
            return ()
        return (ctx.on(self.meta, 'pattern property is not fully anchored', prop.base, iri=iri, pattern=pattern),)


class MissingDescription:
    meta: ClassVar = RuleMeta(
        'missing-description',
        Category.STYLE,
        'entities should have descriptions',
        'Descriptions preserve intent that names and schemas alone cannot communicate.',
        Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ndescription: API\ntypes:\n  T:\n    description: text\n',
        bad='#%RAML 1.0\ntitle: t\ndescription: API\ntypes:\n  T: string\n',
    )

    @staticmethod
    def _finding(ctx: Context, iri: str, entity: Any, name: str) -> tuple[Finding, ...]:
        if entity.description is not None:
            return ()
        return (ctx.on(MissingDescription.meta, 'entity has no description', entity, iri=iri, entity=name),)

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if not is_declaration(iri):
            return ()
        return self._finding(ctx, iri, base, base.name or 'type')

    def api(self, ctx: Context, iri: str, api: APIFragment) -> Iterable[Finding]:
        if api.description is not None:
            return ()
        root = ctx.raml.source_node(api.location)
        position = UNKNOWN if root is None else root.position
        return (
            ctx.at(
                self.meta, 'entity has no description', location=api.location, position=position, iri=iri, entity='API'
            ),
        )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        return self._finding(ctx, iri, endpoint, endpoint.full_uri)

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        return self._finding(ctx, iri, operation, operation.method)

    def response(self, ctx: Context, iri: str, response: Response) -> Iterable[Finding]:
        return self._finding(ctx, iri, response, response.code)

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        return self._finding(ctx, iri, definition, definition.name)


class MissingExample:
    meta: ClassVar = RuleMeta(
        'missing-example',
        Category.STYLE,
        'types and operations should have examples',
        (
            'Examples make the intended wire representation concrete for readers and generated documentation. '
            'RAML 1.0 highly recommends that API documentation include a rich selection of them.'
        ),
        Severity.INFO,
        references=('RAML 1.0 § Defining Examples in RAML',),
        good='#%RAML 1.0\ntitle: t\ntypes:\n  T:\n    type: string\n    example: x\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  T: string\n',
    )

    @staticmethod
    def _has_example(base: BaseShape) -> bool:
        return next(examples_of(base), None) is not None

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        if not is_declaration(iri) or self._has_example(base):
            return ()
        return (ctx.on(self.meta, 'type has no example', base, iri=iri, type=base.name or 'anonymous'),)

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        bodies = list(operation.request.bodies.values()) if operation.request is not None else []
        bodies += [body for response in operation.responses.values() for body in response.bodies.values()]
        if not bodies or any(body.shape is not None and self._has_example(body.shape) for body in bodies):
            return ()
        return (ctx.on(self.meta, 'operation has no payload example', operation, iri=iri, method=operation.method),)


class MissingDisplayName:
    meta: ClassVar = RuleMeta(
        'missing-display-name',
        Category.STYLE,
        'endpoints and operations should have display names',
        'A display name gives generated documentation a stable human-readable label.',
        Severity.INFO,
        good='#%RAML 1.0\ntitle: t\n/a:\n  displayName: A\n  get:\n    displayName: Get A\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  displayName: A\n  get:\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        if endpoint.display_name is not None:
            return ()
        return (ctx.on(self.meta, 'endpoint has no display name', endpoint, iri=iri, endpoint=endpoint.full_uri),)

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        if operation.display_name is not None:
            return ()
        return (ctx.on(self.meta, 'operation has no display name', operation, iri=iri, method=operation.method),)
