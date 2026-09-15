"""Rules about types — docs/18-linting.md § 1 group 1.

Every judgement here follows from RAML's own semantics rather than from taste.
Where that is not obvious, the rule's `rationale` says which part of the
language it follows from, and `tests/unit/test_lint.py` parses its `good` and
`bad` to prove it fires on one and not the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from fastraml.types.complex_ import ObjectShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import AnyShape, NilShape, StringShape
from fastraml.views.graph import is_declaration
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity
from fastraml.yamlnode import NodeKind, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import Body
    from fastraml.parser.fragments import Fragment
    from fastraml.types.base import BaseShape, Parameter, Property
    from fastraml.views.lint.engine import Context

__all__ = [
    'DeprecatedSchemas',
    'DiscriminatorWithoutSubtypes',
    'JsonRefSiblings',
    'MultipleInheritance',
    'OptionalAndNil',
    'UnboundedString',
    'UntypedPayload',
]


class DeprecatedSchemas:
    """The deprecated `schemas:` alias for `types:`."""

    requires_source: ClassVar = True

    meta: ClassVar = RuleMeta(
        id='deprecated-schemas',
        category=Category.SPEC,
        summary='a fragment that declares types with the deprecated schemas key',
        rationale='RAML 1.0 retains `schemas:` for compatibility and explicitly deprecates it in favour of `types:`.',
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  User: string\n',
        bad='#%RAML 1.0\ntitle: t\nschemas:\n  User: string\n',
    )

    def unit(self, ctx: Context, iri: str, fragment: Fragment) -> Iterable[Finding]:
        root = ctx.raml.source_node(fragment.location)
        if root is None:
            # A `type: !include schema.json` is represented by a data-type
            # fragment and a graph unit, but JSON input never had a RAML root
            # node to retain. Only composed RAML sources can spell `schemas:`.
            return ()
        if root.kind is not NodeKind.MAPPING:
            return ()
        for key, _ in pairs(root):
            if key.value == 'schemas':
                return (
                    ctx.at(
                        self.meta,
                        'schemas is deprecated; use types',
                        location=fragment.location,
                        position=key.position,
                        iri=iri,
                        field='schemas',
                    ),
                )
        return ()


class JsonRefSiblings:
    """A draft-07 `$ref` object carrying ignored sibling keywords."""

    meta: ClassVar = RuleMeta(
        id='json-ref-siblings',
        category=Category.SPEC,
        summary='a JSON Schema draft-07 ref with sibling keywords',
        rationale=(
            'The draft-07 resolver RAML applies treats `$ref` as the whole schema object and ignores every '
            'sibling. A constraint written beside it looks active and silently has no effect.'
        ),
        severity=Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\ntypes:\n  User: |\n'
            '    {"definitions":{"Name":{"type":"string"}},"allOf":[{"$ref":"#/definitions/Name"}]}\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\ntypes:\n  User: |\n'
            '    {"definitions":{"Name":{"type":"string"}},'
            '"allOf":[{"$ref":"#/definitions/Name","maxLength":8}]}\n'
        ),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002
        shape = base.shape
        if not isinstance(shape, JsonShape) or shape.base is not base:
            return ()
        if shape.contents is None:
            raise RuntimeError(f'JSON schema shape has no compiled contents: {base.location}')
        stack = [shape.contents]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                siblings = [str(key) for key in value if key != '$ref'] if '$ref' in value else []
                if siblings:
                    return (
                        ctx.at(
                            self.meta,
                            'JSON Schema ref has ignored siblings',
                            location=base.location,
                            position=base.key_pos,
                            iri=iri,
                            siblings=', '.join(siblings),
                        ),
                    )
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        return ()


def _nil_member(base: BaseShape) -> BaseShape | None:
    """The `nil` member of a union, if this shape is one that has one.

    `string?` is sugar for `string | nil` (docs/06), so after P9 both spellings
    arrive here as a `UnionShape` carrying a `NilShape`. The rule below is
    therefore about the *semantics* and not about which spelling was used —
    which is the point: two ways of writing one thing is not a defect, and
    saying a value may be absent twice over is.
    """
    shape = base.shape
    if not isinstance(shape, UnionShape):
        return None
    for member in shape.any_of or ():
        if isinstance(member.shape, NilShape):
            return member
    return None


class OptionalAndNil:
    """`a?: string?` — optional *and* nilable."""

    meta: ClassVar = RuleMeta(
        id='optional-and-nil',
        category=Category.SPEC,
        summary='a property that is both optional and nilable',
        rationale=(
            'RAML has two orthogonal ways to say a value may be absent: `?` on the property name, which '
            'makes the key optional, and `nil` in the type, which makes the value null. Writing both '
            'leaves a consumer unable to tell "the key was omitted" from "the key was present and null", '
            'and nothing in the document says which one the server means.'
        ),
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n',
    )

    def property_(self, ctx: Context, iri: str, prop: Property) -> Iterable[Finding]:
        if prop.required or _nil_member(prop.base) is None:
            return ()
        return (
            ctx.at(
                self.meta,
                'property is both optional and nilable',
                location=prop.base.location,
                position=prop.base.key_pos,
                iri=iri,
                property=prop.name,
            ),
        )


class MultipleInheritance:
    """A declared type with more than one direct supertype."""

    meta: ClassVar = RuleMeta(
        id='multiple-inheritance',
        category=Category.SPEC,
        summary='a type with more than one direct supertype',
        rationale=(
            'Multiple inheritance is legal and its merge is the hardest part of the language to predict: '
            'the result depends on the order the parents are written, on which facets each supplies, and '
            'on whether two of them conflict. It is worth knowing where a document relies on it.'
        ),
        severity=Severity.INFO,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  A: object\n  C:\n    type: A\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  A: object\n  B: object\n  C: [A, B]\n',
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002 - Sink's signature
        # Declarations only. A use site carries its declaration's parents, so an
        # unrestricted rule reports one finding per *use* of a problem rather
        # than one per problem — the mistake docs/16 § 6.2 records for three
        # catalogue queries, which ran and returned plausible rows.
        if len(base.inherits) < 2 or not is_declaration(iri):  # noqa: PLR2004 - "more than one parent" is the rule
            return ()
        return (
            ctx.at(
                self.meta,
                'type inherits from more than one supertype',
                location=base.location,
                position=base.key_pos,
                iri=iri,
                supertypes=', '.join(parent.name or '?' for parent in base.inherits),
            ),
        )


class DiscriminatorWithoutSubtypes:
    """`discriminator:` on a type nothing extends."""

    meta: ClassVar = RuleMeta(
        id='discriminator-without-subtypes',
        category=Category.SPEC,
        summary='a discriminated type that nothing inherits from',
        rationale=(
            'A `discriminator:` names the property a consumer reads to decide which subtype a value is. '
            'With no subtype declared there is nothing to decide, so the facet constrains nothing and '
            'every consumer generating a dispatch from it generates an empty one.'
        ),
        severity=Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\ntypes:\n'
            '  Pet:\n    type: object\n    discriminator: kind\n    properties:\n      kind: string\n'
            '  Dog:\n    type: Pet\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\ntypes:\n'
            '  Pet:\n    type: object\n    discriminator: kind\n    properties:\n      kind: string\n'
        ),
    )

    def type_(self, ctx: Context, iri: str, base: BaseShape, shape_kind: str) -> Iterable[Finding]:  # noqa: ARG002 - Sink's signature
        shape = base.shape
        if not isinstance(shape, ObjectShape) or shape.discriminator is None or not is_declaration(iri):
            return ()
        if any(
            isinstance(parent.shape, ObjectShape) and parent.shape.discriminator is shape.discriminator
            for parent in base.inherits
        ):
            return ()
        # Asked of the graph rather than of the model: a subtype is any node
        # with an `inherits` edge to this one, and the model holds that edge
        # only in the child. Walking every declared type to find them would
        # rebuild the reverse index the graph already is.
        for edge in ctx.graph.into(iri, ('inherits',)):
            if is_declaration(edge.subject):
                return ()
        return (
            ctx.at(
                self.meta,
                'discriminator declared on a type nothing inherits from',
                location=base.location,
                position=shape.discriminator.key_pos,
                iri=iri,
                discriminator=shape.discriminator.value,
            ),
        )


class UntypedPayload:
    """A body whose type is `any`."""

    meta: ClassVar = RuleMeta(
        id='untyped-payload',
        category=Category.SPEC,
        summary='a request or response body typed `any`',
        rationale=(
            '`any` conforms to every value, so a body declared with it constrains nothing and documents '
            'nothing. A payload is the one place where that is never what was meant — it is what a body '
            'gets when its type was left off, not something an author chooses.'
        ),
        severity=Severity.WARNING,
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n'
            '        body:\n          application/json:\n            type: string\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n          application/json:\n',
    )

    def payload(self, ctx: Context, iri: str, body: Body) -> Iterable[Finding]:
        base = body.shape
        if base is None or not isinstance(base.shape, AnyShape) or base.inherits:
            return ()
        return (
            ctx.at(
                self.meta,
                'body constrains nothing',
                location=body.location,
                position=base.key_pos if base.key_pos.is_known else body.key_pos,
                iri=iri,
                mediaType=body.media_type,
            ),
        )


class UnboundedString:
    """A string that arrives from a caller with no upper bound on its size."""

    meta: ClassVar = RuleMeta(
        id='unbounded-string',
        category=Category.SECURITY,
        summary='a string with no maxLength, pattern or enum',
        rationale=(
            'OWASP API4:2023. A string a caller supplies with no size or value restriction leaves the server '
            'to accept an unconstrained allocation. `maxLength` supplies a direct bound; `pattern` and `enum` '
            'record an intentional accepted domain rather than leaving the value unrestricted.'
        ),
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a:\n        type: string\n        maxLength: 64\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a: string\n',
    )

    def _check(self, ctx: Context, iri: str, base: BaseShape, what: str, name: str) -> Iterable[Finding]:
        shape = base.shape
        if not isinstance(shape, StringShape):
            return ()
        if shape.max_length is not None or shape.pattern is not None or base.enum is not None:
            return ()
        return (
            ctx.at(
                self.meta,
                f'{what} is an unbounded string',
                location=base.location,
                position=base.key_pos,
                iri=iri,
                **{what: name},
            ),
        )

    def property_(self, ctx: Context, iri: str, prop: Property) -> Iterable[Finding]:
        return self._check(ctx, iri, prop.base, 'property', prop.name)

    def parameter(self, ctx: Context, iri: str, param: Parameter) -> Iterable[Finding]:
        # A parameter is the stronger case of the two: a property may be a
        # server's own output, while every parameter is input a caller wrote.
        return self._check(ctx, iri, param.base, 'parameter', param.name)
