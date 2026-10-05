"""JSON Schema onto the nearest RAML shape (docs/10-validation.md § 7).

The walk behind `JsonShape.as_shape`: a schema node becomes an `any`, union,
object or the result of the `allOf` reducers, a `$ref` becomes its target's
projection, shared under the target's canonical URI, and a cycle becomes a
`RecursiveShape` back-edge. The shapes built are views and never enter a pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastraml.types.base import (
    TYPE_ANY,
    TYPE_OBJECT,
    TYPE_RECURSIVE,
    TYPE_UNION,
    BaseShape,
    PatternProperty,
    Property,
    ScalarFacet,
)
from fastraml.types.complex_ import ObjectShape, RecursiveShape, UnionShape
from fastraml.types.examples import Example, Examples
from fastraml.types.scalars import AnyShape
from fastraml.types.schema_compile import canonical, draft_of, ref_target, schema_registry
from fastraml.types.schema_intersection import STRING_FORMATS, intersect, ref_siblings_apply
from fastraml.types.schema_view import (
    KEYWORD_TYPE,
    Projection,
    Visiting,
    attach,
    attach_kind,
    check_depth,
    subschema_name,
    try_compile,
    unsupported,
    view_base,
    view_data,
)
from fastraml.types.values import EnumValues

if TYPE_CHECKING:
    # Annotations only: every pattern is compiled through `try_compile`, so
    # the parse's engine applies to projected patterns too (docs/01 § 4.2).
    import re


def project(context: Projection, contents: Any, visiting: Visiting) -> BaseShape:
    """One schema node, projected onto the nearest RAML shape (docs/10 § 7)."""
    # `visiting` holds one entry per level currently open — it is added to
    # before descending and removed in a `finally` — so its size *is* the depth,
    # and the guard costs a `len`. Each document was already bounded by
    # `_check_nesting`; what this catches is a chain of `$ref`s across many
    # shallow documents, which nests as deep as the chain is long.
    check_depth(context, len(visiting))
    if contents is False:
        raise unsupported(context, 'false schema')
    if contents is True or not isinstance(contents, dict):
        return attach_kind(view_base(context), TYPE_ANY, AnyShape)

    reference = contents.get('$ref')
    siblings_apply = False
    if isinstance(reference, str):
        siblings_apply = ref_siblings_apply(context, contents)
    key = id(contents)
    present, previous = key in visiting, visiting.get(key)
    if previous is not None:
        # A schema whose projection is open, reached again without a `$ref` --
        # an `allOf` member's property flattened back into its own subschema.
        # Re-entering it would not grow `visiting`, so neither the depth guard
        # nor anything else would stop the walk: it is the cycle's back-edge.
        return _back_edge(context, previous)
    # A productive recursive target may revisit its caller's reference wrapper.
    # Restore that frame on return; it is not owned by the nested traversal.
    base = None if isinstance(reference, str) and not siblings_apply else _decorate(view_base(context), contents)
    visiting[key] = base
    try:
        if isinstance(reference, str) and not siblings_apply:
            return project_reference(context, reference, visiting)
        assert base is not None  # noqa: S101 - only reference-only frames have no type head
        if siblings_apply:
            return intersect(context, contents, base, visiting, project)
        if 'if' in contents:
            raise unsupported(context, 'if/then/else')
        return _project_body(context, contents, base, visiting)
    finally:
        if present:
            visiting[key] = previous
        else:
            del visiting[key]


def project_reference(context: Projection, reference: str, visiting: Visiting) -> BaseShape:
    resolved = context.resolver.lookup(reference)
    # A reference moves the walk outright rather than deeper, so where it lands
    # replaces the current position instead of extending it.
    document, target = ref_target(context.resolver, reference)
    if id(resolved.contents) in visiting:
        head = _cycle_head(visiting, id(resolved.contents))
        if head is None:
            raise unsupported(context, 'reference-only cycle')
        return _back_edge(context, head)

    uri = f'{document}#{target}'
    # Named by the rule `view_base` uses, so a target reached through a `$ref`
    # and the same target reached by descent agree. A reference with no pointer
    # names the whole document, which `view_base` already calls after its file.
    name = subschema_name(uri) if target else None
    if name is not None:
        existing = context.defs.get(name)
        if existing is not None and existing.location == uri:
            return existing
    # Only a target in a file of its own has a canonical URI to share under. An
    # inline schema compiles under the RAML file's URI, which it shares with
    # every other inline schema in that file.
    shared_as = canonical(context.parent._raml, document, target)  # noqa: SLF001 - the parse's index
    registry = schema_registry(context.parent._raml)  # noqa: SLF001 - one registry per parse
    draft = draft_of(context.validator)
    shared = registry.projected(shared_as, draft) if shared_as else None
    if shared is not None:
        if name is not None:
            context.defs[name] = shared[0]
        return shared[0]

    built = project(context.at(resolved.resolver, target), resolved.contents, visiting)
    if name is not None:
        built.name = name
        context.defs[name] = built
    if shared_as is not None and not isinstance(built.shape, RecursiveShape):
        # After the walk, never during it: a shape still being built is one a
        # cycle must reach through `visiting`. Nor a back-edge: a reference
        # that is one names a head only this walk has open, so the same target
        # walked from another entry is the head's projection, not a marker.
        registry.share(shared_as, draft, built)
    return built


def _back_edge(context: Projection, head: BaseShape) -> BaseShape:
    """The back-edge of a cycle, which is exactly what P9 produces for a
    recursive RAML type (docs/07 § 6).
    """
    base = view_base(context, head.name)
    base.type = TYPE_RECURSIVE
    base.shape = RecursiveShape(base, head)
    return base


def _cycle_head(visiting: Visiting, key: int) -> BaseShape | None:
    """The type head a cycle back to the frame `key` passes through, or `None`.

    A reference-only frame has no head of its own, but the frames opened after
    it are the cycle, and the first of them with a head is what the reference
    resolves to. Only a cycle of reference-only frames is unproductive, so
    whether one fails does not depend on which of its schemas the walk entered
    by (docs/10 § 7). `visiting` is in the order the frames were opened.
    """
    head = visiting[key]
    if head is not None:
        return head
    after = False
    for frame, opened in visiting.items():
        if frame == key:
            after = True
        elif after and opened is not None:
            return opened
    return None


def _project_body(context: Projection, contents: dict, base: BaseShape, visiting: Visiting) -> BaseShape:
    if contents.get('allOf'):
        # Intersect source constraints rather than directional RAML inheritance.
        return intersect(context, contents, base, visiting, project)
    for keyword in ('oneOf', 'anyOf'):
        # `oneOf`'s exactly-one semantics is lost. RAML's union is "at least
        # one" and there is nothing nearer; docs/10 § 7 records the loss.
        members = contents.get(keyword)
        if members:
            if contents.get('format') in STRING_FORMATS:
                # A format beside a disjunction constrains the complete result;
                # the ordinary union projector does not distribute sibling facets.
                raise unsupported(context, f'{keyword} with format')
            return _project_union(context, keyword, members, base, visiting)

    declared = contents.get('type') or _inferred_type(contents)
    if declared is None and not contents.keys() & {'enum', 'const'} and contents.get('format') not in STRING_FORMATS:
        return attach_kind(base, TYPE_ANY, AnyShape)
    if declared == 'object' and 'patternProperties' in contents:
        return _project_object(context, contents, base, visiting)
    return intersect(context, contents, base, visiting, project, strict=False)


def _inferred_type(contents: dict) -> str | None:
    """The one kind a subschema constrains, when it constrains only one.

    **This is a projection decision, not JSON Schema semantics.** In JSON Schema
    a keyword is an assertion that applies *conditionally on the instance type*:
    `{"properties": {...}, "required": ["a"]}` does not say the instance is an
    object, it says that **if** it is one then `a` must be present. Against the
    string `"hello"` that schema passes, vacuously. Nothing is inferred, and
    validation here is unaffected — it goes to the real validator, which has
    those semantics.

    The projection has to pick a kind, because RAML has no way to spell
    "a constraint that applies only to objects, and is silent otherwise". When
    every keyword present points at one kind, that kind is the least-lossy pick.

    When they point at more than one — `{"properties": {...}, "minLength": 3}`
    constrains objects *and* strings, which is legal and which RAML cannot
    express — this returns `None` and the shape stays `any` rather than silently
    choosing. Losing the constraints is bad; claiming the wrong kind is worse.

    `allOf` selects a type from the whole intersection before projecting its
    conditional constraints (docs/10 § 7).
    """
    implied = {KEYWORD_TYPE[keyword] for keyword in contents if keyword in KEYWORD_TYPE}
    return implied.pop() if len(implied) == 1 else None


def _project_union(context: Projection, keyword: str, members: list, base: BaseShape, visiting: Visiting) -> BaseShape:
    projected = [project(context.into(keyword, str(i)), member, visiting) for i, member in enumerate(members)]
    return attach_kind(base, TYPE_UNION, UnionShape, any_of=projected)


def _project_object(context: Projection, contents: dict, base: BaseShape, visiting: Visiting) -> BaseShape:
    extras = contents.get('additionalProperties')
    if isinstance(extras, dict):
        raise unsupported(context, 'schema-form additionalProperties')

    required = set(contents.get('required') or ())
    properties = {
        name: Property(
            name=name, base=project(context.into('properties', name), schema, visiting), required=name in required
        )
        for name, schema in (contents.get('properties') or {}).items()
    }
    patterns: dict[str, PatternProperty] = {}
    for text, schema in (contents.get('patternProperties') or {}).items():
        compiled = _compile(context, text)
        patterns[f'/{text}/'] = PatternProperty(
            pattern=compiled, base=project(context.into('patternProperties', text), schema, visiting)
        )

    shape = ObjectShape(base, properties=properties or None, pattern_properties=patterns or None)
    shape.min_properties = _int_facet(base, contents.get('minProperties'))
    shape.max_properties = _int_facet(base, contents.get('maxProperties'))
    if isinstance(extras, bool):
        shape.additional_properties = ScalarFacet(value=extras, location=base.location)
    return attach(base, TYPE_OBJECT, shape)


def _decorate(base: BaseShape, contents: dict) -> BaseShape:
    """The five annotation-ish keywords that map onto common RAML facets."""
    title = contents.get('title')
    if isinstance(title, str):
        base.display_name = ScalarFacet(value=title, location=base.location)
    description = contents.get('description')
    if isinstance(description, str):
        base.description = ScalarFacet(value=description, location=base.location)
    if 'default' in contents:
        base.default = view_data(base, contents['default'])
    enum = contents.get('enum')
    if isinstance(enum, list):
        base.enum = EnumValues(view_data(base, member) for member in enum)
    examples = contents.get('examples')
    if isinstance(examples, list) and examples:
        base.examples = Examples(
            location=base.location,
            _values={
                str(index): Example(
                    id=base._raml.next_id(),  # noqa: SLF001 - one counter per parse
                    name=str(index),
                    location=base.location,
                    data=view_data(base, value),
                )
                for index, value in enumerate(examples)
            },
        )
    return base


def _int_facet(base: BaseShape, value: Any) -> ScalarFacet[int] | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return ScalarFacet(value=value, location=base.location)


def _compile(context: Projection, text: str) -> re.Pattern[str]:
    compiled = try_compile(context, text)
    if compiled is None:
        raise unsupported(context, f'patternProperties: {text}')
    return compiled
