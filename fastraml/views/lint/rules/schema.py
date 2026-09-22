"""Rules about types and payloads — docs/18-linting.md § 1.

The `spec` rules here follow from RAML's own semantics rather than from taste;
where that is not obvious, the rule's `rationale` says which part of the
language it follows from. The `style` rules judge legal type designs worth a
review. `tests/unit/test_lint.py` parses every rule's `good` and `bad` to prove
it fires on one and not the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from fastraml.nodes import TypeNode
from fastraml.positions import UNKNOWN
from fastraml.types.complex_ import ObjectShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape, escape_json_pointer_segment, projected
from fastraml.types.scalars import AnyShape, FileShape, NilShape
from fastraml.views.graph import is_declaration
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity
from fastraml.yamlnode import NodeKind, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import Body
    from fastraml.parser.fragments import Fragment
    from fastraml.types.base import BaseShape, Property
    from fastraml.views.lint.engine import Context

__all__ = [
    'DeprecatedSchemas',
    'DiscriminatorWithoutSubtypes',
    'JsonRefSiblings',
    'MeaninglessMediaTypeSchema',
    'MultipleInheritance',
    'OptionalAndNil',
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
        references=('RAML 1.0 § The Root of the Document',),
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
        references=('JSON Schema draft-07 § 8.3',),
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

    def run(self, ctx: Context) -> Iterable[Finding]:
        found = []
        seen: set[tuple[str, str]] = set()
        for iri, node in ctx.graph.nodes.items():
            if not isinstance(node, TypeNode) or not isinstance(shape := node.entity.shape, JsonShape):
                continue
            base = node.entity
            if shape.contents is None:
                raise RuntimeError(f'JSON schema shape has no compiled contents: {base.location}')
            canonical = shape.canonical_uri or ''
            document, _, root_pointer = canonical.partition('#')
            schema = document or shape.document_uri or base.location
            external = shape.document_uri is not None
            identity = schema if external else f'{base.location}:{base.id}'
            stack = [(shape.contents, root_pointer)]
            while stack:
                value, pointer = stack.pop()
                if isinstance(value, dict):
                    siblings = [str(key) for key in value if key != '$ref'] if '$ref' in value else []
                    schema_path = f'#{pointer}/$ref'
                    if siblings and (identity, schema_path) not in seen:
                        seen.add((identity, schema_path))
                        found.append(
                            ctx.at(
                                self.meta,
                                'JSON Schema $ref has ignored sibling keywords',
                                location=schema if external else base.location,
                                position=UNKNOWN if external else base.key_pos,
                                iri=iri,
                                schemaPath=schema_path,
                                siblings=', '.join(siblings),
                            )
                        )
                    stack.extend(
                        (child, f'{pointer}/{escape_json_pointer_segment(str(key))}') for key, child in value.items()
                    )
                elif isinstance(value, list):
                    stack.extend((child, f'{pointer}/{index}') for index, child in enumerate(value))
        return found


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
        category=Category.STYLE,
        summary='a property that is both optional and nilable',
        rationale=(
            'RAML distinguishes an omitted key from a present null value, so this creates a three-state field. '
            'That is useful for PATCH-like contracts but is often accidental elsewhere and deserves review.'
        ),
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string\n',
        bad='#%RAML 1.0\ntitle: t\ntypes:\n  U:\n    properties:\n      a?: string?\n',
    )

    def property_(self, ctx: Context, iri: str, prop: Property) -> Iterable[Finding]:
        if prop.required or _nil_member(prop.base) is None:
            return ()
        return (ctx.on(self.meta, 'property is both optional and nilable', prop.base, iri=iri, property=prop.name),)


class MultipleInheritance:
    """A declared type with more than one direct supertype."""

    meta: ClassVar = RuleMeta(
        id='multiple-inheritance',
        category=Category.STYLE,
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
            ctx.on(
                self.meta,
                'type inherits from more than one supertype',
                base,
                iri=iri,
                supertypes=', '.join(parent.name or '?' for parent in base.inherits),
            ),
        )


class DiscriminatorWithoutSubtypes:
    """`discriminator:` on a type nothing extends."""

    meta: ClassVar = RuleMeta(
        id='discriminator-without-subtypes',
        category=Category.STYLE,
        summary='a discriminated type that nothing inherits from',
        rationale=(
            'A `discriminator:` names the property a consumer reads to decide which subtype a value is. '
            'With no subtype declared in the document there is nothing local to dispatch to. The facet can '
            'still describe an external extension point, so this is an opt-in design warning.'
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
            ctx.on(
                self.meta,
                'discriminator declared on a type nothing inherits from',
                base,
                iri=iri,
                position=shape.discriminator.key_pos,
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
        references=('RAML 1.0 § Determine Default Types',),
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
            ctx.on(
                self.meta,
                'body constrains nothing',
                body,
                iri=iri,
                position=base.key_pos if base.key_pos.is_known else body.key_pos,
                mediaType=body.media_type,
            ),
        )


class MeaninglessMediaTypeSchema:
    """A request or response shape that contradicts the representation's media type."""

    meta: ClassVar = RuleMeta(
        id='meaningless-media-type-schema',
        category=Category.SPEC,
        summary='a body media type whose schema cannot represent that media',
        rationale=(
            'The media type tells the reader how to decode bytes before the schema is applied, so binary media '
            'needs a file value, form encodings need named object fields, and plain text needs a scalar. A '
            '`file` in a JSON or XML body is not reported: RAML represents it as a base64-encoded string.'
        ),
        severity=Severity.WARNING,
        references=(
            'RAML 1.0 § File',
            'RFC 6839 § 3.6',
            'RFC 7578',
            'RFC 8081',
            'RFC 8460 § 6.3',
            'RFC 8949 § 9.3',
            'RFC 8949 § 9.5',
        ),
        good=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/json:\n            type: object\n            properties:\n              id: string\n'
        ),
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/octet-stream:\n            type: object\n'
        ),
    )

    def payload(self, ctx: Context, iri: str, body: Body) -> Iterable[Finding]:
        base = body.shape
        issue = _media_type_issue(base, body.media_type.partition(';')[0].strip().casefold())
        if issue is None:
            return ()
        return (
            ctx.on(
                self.meta,
                'body schema is meaningless for its media type',
                body,
                iri=iri,
                mediaType=body.media_type,
                type=base.type if base is not None else 'none',
                reason=issue,
            ),
        )


_BINARY_MEDIA = frozenset(
    {
        'application/cbor',
        'application/gzip',
        'application/octet-stream',
        'application/pdf',
        'application/zip',
    }
)
#: Top-level types whose every subtype is binary; `font` is RFC 8081.
_BINARY_TOP_LEVEL = ('audio/', 'font/', 'image/', 'video/')
#: Structured syntax suffixes naming a binary encoding: RFC 6839 §§ 3.2-3.6, RFC 8460 § 6.3, RFC 8949 § 9.5.
_BINARY_SUFFIXES = ('+ber', '+cbor', '+der', '+fastinfoset', '+gzip', '+wbxml', '+zip')
_FORM_MEDIA = frozenset({'application/x-www-form-urlencoded', 'multipart/form-data'})


def _matches_media_type(pattern: str, media_type: str) -> bool:
    pattern = pattern.partition(';')[0].strip().casefold()
    return pattern == media_type or (pattern.endswith('/*') and media_type.startswith(pattern[:-1]))


def _media_type_issue(base: BaseShape | None, media_type: str) -> str | None:  # noqa: PLR0911 - compatibility matrix
    if base is None or base.shape is None or isinstance(base.shape, AnyShape):
        return None
    shape = base.shape
    if isinstance(shape, UnionShape):
        for member in shape.any_of or ():
            if (issue := _media_type_issue(member, media_type)) is not None:
                return issue
        return None
    if isinstance(shape, JsonShape):
        projection = projected(base)
        return None if projection is base else _media_type_issue(projection, media_type)
    if isinstance(shape, FileShape):
        if shape.file_types and not any(_matches_media_type(facet.value, media_type) for facet in shape.file_types):
            return 'fileTypes excludes the declared media type'
        return None
    if media_type in _BINARY_MEDIA or media_type.startswith(_BINARY_TOP_LEVEL) or media_type.endswith(_BINARY_SUFFIXES):
        return 'binary media requires a file shape'
    if media_type in _FORM_MEDIA and not isinstance(shape, ObjectShape):
        return 'form media requires an object shape'
    if media_type == 'text/plain' and not shape.is_scalar():
        return 'plain text requires a scalar or file shape'
    return None
