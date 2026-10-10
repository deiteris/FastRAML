"""`make_shape` — the one way a declaration becomes a shape.

Every property, header, query parameter, body, URI parameter, type declaration
and inline declaration goes through here. The walk is docs/05-type-model.md
§ 3: one pass over the mapping, common facets peeled off into the
`BaseShape`, everything else left in a flat `[k0, v0, k1, v1, …]` list for the
kind to read.

Kind dispatch lives here too, so this module imports the kind modules and they
must not import it back. A kind that holds declarations publishes a
`DECLARATION_FACETS` table; this module reads it, constructs the kind, and
builds those children into it.

Nothing here resolves. A type expression, a named reference and multiple
inheritance all leave an `UnknownShape` on `Raml.unresolved_shapes` for P7.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final, cast

from fastraml import facet_names as fn
from fastraml.datanode import make_data_node
from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.facet_names import chomp_optional
from fastraml.parser.annotations import add_domain_extension, is_annotation_key
from fastraml.parser.facets import (
    annotated_scalar_value,
    compile_pattern,
    make_annotated_data_facet,
    make_bool_facet,
    make_string_facet,
    resolve_annotated_scalar,
    scalar_str,
)
from fastraml.parser.includes import content_include, inline_include, note_include_ref
from fastraml.parser.substitutions import substituted_site
from fastraml.types.base import (
    BUILTIN_TYPES,
    TYPE_ANY,
    TYPE_ARRAY,
    TYPE_COMPOSITE,
    TYPE_JSON,
    TYPE_OBJECT,
    TYPE_STRING,
    BaseShape,
    Binding,
    KindBase,
    Parameter,
    PatternProperty,
    Property,
    Shape,
    TypeExprRef,
    declaration_facets,
    owned,
)
from fastraml.types.complex_ import (
    ArrayShape,
    ObjectShape,
    UnionShape,
    UnknownShape,
)
from fastraml.types.examples import Examples, make_example
from fastraml.types.inference import identify_shape_type
from fastraml.types.jsonschema_ import JsonShape
from fastraml.types.scalars import (
    AnyShape,
    BooleanShape,
    DateOnlyShape,
    DateTimeOnlyShape,
    DateTimeShape,
    FileShape,
    IntegerShape,
    NilShape,
    NumberShape,
    StringShape,
    TimeOnlyShape,
)
from fastraml.types.values import EnumValues
from fastraml.types.xml import decode_xml_serialization
from fastraml.yamlnode import TAG_INCLUDE, TAG_NULL, TAG_STR, NodeKind, is_null, node_error, pairs

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastraml.parser.fragments import DataTypeFragment, NamedExample
    from fastraml.registry import ParseCtx, Raml
    from fastraml.yamlnode import Node

__all__ = [
    'COMMON_FACETS',
    'TYPE_SPECIFIC_FACETS',
    'attach_kind',
    'make_body_shape',
    'make_declarations',
    'make_parameter_map',
    'make_pattern_property',
    'make_property',
    'make_shape',
    'unmarshal_types',
]

#: Kind name to the class that implements it. A name absent from this table is
#: a reference or a type expression, and becomes an `UnknownShape` for P7.
KIND_TO_CLASS: Final[dict[str, type[KindBase]]] = {
    'any': AnyShape,
    'nil': NilShape,
    'null': NilShape,
    'boolean': BooleanShape,
    'string': StringShape,
    'integer': IntegerShape,
    'number': NumberShape,
    'datetime': DateTimeShape,
    'datetime-only': DateTimeOnlyShape,
    'date-only': DateOnlyShape,
    'time-only': TimeOnlyShape,
    'file': FileShape,
    'object': ObjectShape,
    'array': ArrayShape,
    'union': UnionShape,
    'json': JsonShape,
}

#: Built-in facets every kind has: exactly the keys `_decode` consumes. A
#: `facets:` declaration may not shadow one (docs/05 § 5). `strict` and
#: `value` are example-level keys, not type facets, and are deliberately absent.
COMMON_FACETS: Final = frozenset(
    {
        fn.FACET_TYPE,
        fn.FACET_SCHEMA,
        fn.FACET_FACETS,
        fn.FACET_EXAMPLE,
        fn.FACET_EXAMPLES,
        fn.FACET_DEFAULT,
        fn.FACET_DESCRIPTION,
        fn.FACET_DISPLAY_NAME,
        fn.FACET_REQUIRED,
        fn.FACET_ENUM,
        fn.FACET_XML,
        fn.FACET_ALLOWED_TARGETS,
    }
)

_LENGTHS: Final = frozenset({fn.FACET_MIN_LENGTH, fn.FACET_MAX_LENGTH})
_NUMERIC: Final = frozenset({fn.FACET_MINIMUM, fn.FACET_MAXIMUM, fn.FACET_MULTIPLE_OF, fn.FACET_FORMAT})

#: Built-in facets of one kind: exactly the keys its declaration table and its
#: `decode_facets` consume, so a `facets:` declaration on that kind may not
#: shadow them either. `test_shape_decode.py` derives this from the decoders
#: and fails on drift.
TYPE_SPECIFIC_FACETS: Final[dict[str, frozenset[str]]] = {
    'object': frozenset(
        {
            fn.FACET_PROPERTIES,
            fn.FACET_ADDITIONAL_PROPERTIES,
            fn.FACET_MIN_PROPERTIES,
            fn.FACET_MAX_PROPERTIES,
            fn.FACET_DISCRIMINATOR,
            fn.FACET_DISCRIMINATOR_VALUE,
        }
    ),
    'array': frozenset({fn.FACET_ITEMS, fn.FACET_MIN_ITEMS, fn.FACET_MAX_ITEMS, fn.FACET_UNIQUE_ITEMS}),
    'union': frozenset({fn.FACET_ANY_OF}),
    'string': _LENGTHS | {fn.FACET_PATTERN},
    'integer': _NUMERIC,
    'number': _NUMERIC,
    'datetime': frozenset({fn.FACET_FORMAT}),
    'file': _LENGTHS | {fn.FACET_FILE_TYPES},
}


def make_shape(  # noqa: PLR0913 - the declaration, plus where to attach it
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    default_type: str = TYPE_STRING,
    *,
    attach: Callable[[BaseShape], None] | None = None,
) -> BaseShape:
    """Build one declaration. `default_type` applies when nothing else settles it.

    `attach` puts the shape where it belongs as soon as it exists, before its
    content is decoded. A shape that then fails stays there, marked in
    `Raml.broken` (docs/13 § 1); without `attach`, a failure leaves nothing.
    """
    scope = _declaration_scope(raml, value_node)
    if scope is None:
        return _make_shape(raml, key_node, value_node, raml.location_of(value_node, location), default_type, attach)
    # A node produced by parameter substitution, or grafted from a trait or a
    # resource type, resolves its unqualified names in the namespace recorded
    # for it — not the applying document's (docs/08 § 4.2). Pushed around
    # the whole build, so nested facets inherit it.
    raml.push_ctx(scope)
    try:
        return _make_shape(raml, key_node, value_node, raml.location_of(value_node, location), default_type, attach)
    finally:
        raml.pop_ctx()


def _declaration_scope(raml: Raml, node: Node) -> ParseCtx | None:
    """The type name's own namespace wins over its containing declaration.

    Syntax selection belongs to this decoder; the registry only looks up each
    candidate node's provenance (docs/08 § 4.2).
    """
    if not raml.has_provenance:
        return None
    if node.kind is NodeKind.MAPPING:
        content = node.content
        for index in range(0, len(content), 2):
            if content[index].value not in (fn.FACET_TYPE, fn.FACET_SCHEMA):
                continue
            type_node = content[index + 1]
            value = annotated_scalar_value(type_node)
            if value is not None and value is not type_node:
                scope = raml.scope_for(value)
                if scope is not None:
                    return scope
            scope = raml.scope_for(type_node)
            if scope is not None:
                return scope
    return raml.scope_for(node)


def _make_shape(  # noqa: PLR0913, PLR0917 - make_shape's arguments, resolved
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    default_type: str,
    attach: Callable[[BaseShape], None] | None,
) -> BaseShape:
    position_node = key_node if key_node is not None else value_node
    base = BaseShape(
        id=raml.next_id(),
        raml=raml,
        location=location,
        name=key_node.value if key_node is not None else None,
        key_pos=position_node.position,
        value_pos=value_node.full_position,
        anchor=raml.current_ctx().anchor,
        is_annotation_type=raml.current_ctx().target is DomainLocation.ANNOTATION_TYPE,
    )
    raml.put_source_info(base.id, key_node, value_node)
    if attach is None:
        return _decode_shape(raml, base, value_node, location, default_type)

    attach(base)
    try:
        return _decode_shape(raml, base, value_node, location, default_type)
    except RamlError as err:
        # Attached, so it stays in the model: marked, registered, and never
        # without a kind. A failure before one was settled leaves the same
        # `UnknownShape` an unresolved name does (docs/13 § 1).
        if base.shape is None:
            base.shape = UnknownShape(base, from_mapping=value_node.kind is NodeKind.MAPPING)
        raml.mark(base, err)
        raml.put_shape(base)
        raise


def _decode_shape(raml: Raml, base: BaseShape, value_node: Node, location: str, default_type: str) -> BaseShape:
    """Everything `make_shape` reads from the declaration's value."""
    type_node, facets = _decode(raml, base, value_node)
    base.type_written = type_node is not None and (
        value_node.kind is not NodeKind.SCALAR
        or not (is_null(type_node) or (type_node.tag == TAG_STR and not type_node.value))
    )
    if type_node is None:
        kind = identify_shape_type(facets, default_type, location)
    else:
        kind, prebuilt = _decode_type_node(raml, base, type_node, facets, default_type)
        if prebuilt is not None:
            base.type = kind
            base.shape = prebuilt
            prebuilt.decode_facets(facets)
            raml.put_shape(base)
            return base

    # A mapping declaration narrows whatever its `type:` names; a bare scalar or
    # sequence one has nothing to narrow with. See docs/06 § 3;
    # only an UnknownShape reads the flag.
    attach_kind(raml, base, kind, facets, from_mapping=value_node.kind is NodeKind.MAPPING)
    raml.put_shape(base)
    if isinstance(base.shape, UnknownShape):
        # Invariant I4: P7 drains this worklist and swaps in the real kind.
        raml.unresolved_shapes.append(base)
    return base


def unmarshal_types(
    raml: Raml, node: Node, location: str, declared: dict[str, BaseShape], *, is_annotation: bool = False
) -> None:
    """Decode a `types:`, `schemas:` or `annotationTypes:` mapping into `declared`.

    Per name: reject a built-in name, build the shape, register it under the
    file, and append it to the flat per-file index that unwrap and validation
    iterate (docs/04 § 5).

    Errors accumulate, so one bad declaration does not hide the rest. The
    fragment's own map is filled before they are raised, so it lists the same
    declarations as the registry (docs/11 § 2).
    """
    # Named in the declaring document, whichever file the map is written in.
    namespace = location
    node, location = inline_include(raml, node, location)
    if is_null(node):
        # `types:` with nothing under it. RAML uses an empty value widely.
        return
    if node.kind is not NodeKind.MAPPING:
        raise node_error('type declarations must be a mapping', location, node)

    accumulator = Accumulator()
    # An annotation written on one of these declarations targets the
    # declaration, not the file that holds it (docs/09 § B4).
    target = DomainLocation.ANNOTATION_TYPE if is_annotation else DomainLocation.TYPE_DECLARATION
    with raml.target_scope(target):
        for key, value in pairs(node):
            name = key.value
            try:
                if name in BUILTIN_TYPES:
                    raise node_error('cannot redefine a built-in type', location, key, info={'type': name})
                make_shape(
                    raml, key, value, location, attach=partial(_declare, raml, declared, namespace, is_annotation)
                )
            except RamlError as err:
                accumulator.add(err)
    accumulator.raise_if_any()


def _declare(raml: Raml, declared: dict[str, BaseShape], namespace: str, is_annotation: bool, base: BaseShape) -> None:  # noqa: FBT001 - bound by `partial`
    """Register a declaration under its name in `namespace`, the declaring
    document, before its content is decoded. The flat index that unwrap and
    validation iterate is by the file it is written in.
    """
    name = cast('str', base.name)
    base.is_annotation_type = is_annotation
    declared[name] = base
    if is_annotation:
        raml.put_annotation_type(name, namespace, base)
    else:
        raml.put_type(name, namespace, base)
    raml.put_typedef(base.location, base)


def make_body_shape(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> BaseShape:
    """`make_shape` for a `body:` node, whose default type is `any`.

    Spec section Determine Default Types: "The default type `any` is applied to
    any `body` node that does not contain `properties`, `type`, or `schema`."
    """
    return make_shape(raml, key_node, value_node, location, TYPE_ANY)


# -- the declaration walk (docs/05 § 3) ------------------------------------


def _decode(  # noqa: PLR0912 - one pass over the common-facet vocabulary (docs/05 § 3)
    raml: Raml, base: BaseShape, value_node: Node
) -> tuple[Node | None, list[Node]]:
    """One pass over a declaration, returning the type node and the leftovers."""
    if value_node.kind is not NodeKind.MAPPING:
        # A scalar or a sequence is a type expression or multiple inheritance,
        # with no facets of its own.
        return value_node, []

    type_node: Node | None = None
    facets: list[Node] = []
    location = base.location
    content = value_node.content
    for index in range(0, len(content), 2):
        key, value = content[index], content[index + 1]
        match key.value:
            case fn.FACET_TYPE | fn.FACET_SCHEMA:
                if type_node is not None:
                    raise node_error('type and schema are mutually exclusive', location, key)
                type_node = value
                if key.value == fn.FACET_SCHEMA:
                    raml.record_syntax_alias(base, key, location)
            case fn.FACET_DISPLAY_NAME:
                base.display_name = make_string_facet(raml, key, value, location)
            case fn.FACET_DESCRIPTION:
                base.description = make_string_facet(raml, key, value, location)
            case fn.FACET_REQUIRED:
                base.required = make_bool_facet(raml, key, value, location)
            case fn.FACET_FACETS:
                raml.record_section(base, key, value, location)
                _decode_custom_facet_defs(raml, base, value)
            case fn.FACET_EXAMPLE:
                _decode_example(raml, base, key, value)
            case fn.FACET_EXAMPLES:
                _decode_examples(raml, base, value)
            case fn.FACET_DEFAULT:
                base.default = make_annotated_data_facet(raml, key, value, location)
            case fn.FACET_ENUM:
                base.enum = _decode_enum(raml, value, location)
            case fn.FACET_XML:
                base.xml = decode_xml_serialization(raml, value, location)
            case fn.FACET_ALLOWED_TARGETS:
                if not base.is_annotation_type:
                    raise node_error('allowedTargets is only valid on annotation types', location, key)
                base.allowed_targets = _decode_allowed_targets(raml, value, location)
            case name if is_annotation_key(name):
                base.annotations = owned(base.annotations)
                add_domain_extension(raml, base.annotations, location, key, value)
            case _:
                facets.append(key)
                facets.append(value)
    return type_node, facets


def _decode_allowed_targets(raml: Raml, value_node: Node, location: str) -> list[DomainLocation]:
    """`allowedTargets:` — one target name or a sequence of them (docs/09 § B4).

    The result is a list either way, but an *absent* facet stays `None` on the
    base: absent means any target is allowed and empty means none is, and P10
    has to tell them apart.
    """
    value_node, location = inline_include(raml, value_node, location)
    items = value_node.content if value_node.kind is NodeKind.SEQUENCE else [value_node]
    targets: list[DomainLocation] = []
    accumulator = Accumulator()
    for item in items:
        # Positioned at the offending value, not at the `allowedTargets` key:
        # in a sequence of six the key says nothing about which one is wrong.
        try:
            targets.append(DomainLocation(scalar_str(item, location)))
        except ValueError:
            accumulator.add(node_error('unknown annotation target', location, item, info={'target': item.value}))
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()
    return targets


def _decode_enum(raml: Raml, value_node: Node, location: str) -> list:
    value_node, location = inline_include(raml, value_node, location)
    if value_node.kind is not NodeKind.SEQUENCE:
        raise node_error('enum must be a sequence', location, value_node)
    return EnumValues(make_data_node(raml, None, item, location) for item in value_node.content)


def _decode_example(raml: Raml, base: BaseShape, key: Node, value_node: Node) -> None:
    if base.examples is not None:
        raise node_error('example and examples cannot be defined together', base.location, value_node)
    base.example = make_example(raml, key, value_node, '', base.location)


def _decode_examples(raml: Raml, base: BaseShape, value_node: Node) -> None:
    if base.example is not None:
        raise node_error('example and examples cannot be defined together', base.location, value_node)
    position, location = value_node.full_position, base.location
    content = content_include(raml, value_node, location)
    if content is not None:
        # A file without a header is the map of examples, written there.
        value_node, location = content
    elif value_node.tag == TAG_INCLUDE:
        base.examples = Examples(
            location=base.location,
            position=position,
            link=_parse_named_example(raml, base, value_node),
        )
        return
    if is_null(value_node):
        return
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('examples must be a mapping', location, value_node)
    values = {key.value: make_example(raml, key, value, key.value, location) for key, value in pairs(value_node)}
    base.examples = Examples(location=base.location, position=position, _values=values)


def _decode_custom_facet_defs(raml: Raml, base: BaseShape, value_node: Node) -> None:
    """`facets:` — a properties declaration, so it reuses `make_property`."""
    value_node, location = inline_include(raml, value_node, base.location)
    if is_null(value_node):
        return
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('facets must be a mapping', location, value_node)
    for key, value in pairs(value_node):
        if key.value.startswith('('):
            # Otherwise a facet name would be ambiguous with an annotation.
            raise node_error("facet name must not begin with '('", location, key, info={'facet': key.value})
        prop = make_property(raml, key, value, location)
        base.custom_facet_defs = owned(base.custom_facet_defs)
        base.custom_facet_defs[prop.name] = prop


# -- what type is this? (docs/05 § 3) --------------------------------------


def _decode_type_node(
    raml: Raml,
    base: BaseShape,
    type_node: Node,
    facets: list[Node],
    default_type: str,
) -> tuple[str, Shape | None]:
    """Read the `type:` node. Returns the kind, and a shape when it built one."""
    location = base.location
    if type_node.kind is NodeKind.MAPPING:
        type_node, _annotations = resolve_annotated_scalar(raml, type_node, location)
    if type_node.kind is NodeKind.SEQUENCE:
        base.inherits = [_inherited(raml, item, location) for item in type_node.content]
        return TYPE_COMPOSITE, None
    if type_node.kind is not NodeKind.SCALAR:
        raise node_error('type must be a string or a sequence', location, type_node)

    # Retained whole, so P7 can report a column inside the expression and tooling
    # can offer go-to-definition on each name within it.
    base.type_expr = type_node

    if type_node.tag == TAG_INCLUDE:
        # `_parse_data_type` records the include.
        base.link = _parse_data_type(raml, base, type_node)
        return '', None
    if type_node.tag == TAG_NULL:
        # `default_type`, not `TYPE_STRING`: for a `type:` written with no value
        # the two are the same, but a `body:` declared with nothing under it is
        # `any` — spec section Determine Default Types, "the default type `any`
        # is applied to any body node that does not contain properties, type, or
        # schema".
        return default_type, None
    if type_node.tag != TAG_STR:
        # A caller's `max: 1` substituted whole keeps its type (docs/08 § 5),
        # and is reported where the caller wrote it.
        site = substituted_site(raml.substitutions, type_node, 0, len(type_node.value))
        if site is not None:
            raise RamlError.new('type must be a string', *site)
        raise node_error('type must be a string', location, type_node)

    text = type_node.value
    if not text:
        return identify_shape_type(facets, default_type, location), None
    if text.lstrip()[:1] == '{':
        # An inline JSON Schema. `decode_json_schema` has already wrapped an
        # external .json file into this same form (docs/04 § 5). JSON allows
        # whitespace before the value, and a file indented as a whole has it.
        return TYPE_JSON, JsonShape(base, raw=text)
    if text in BUILTIN_TYPES:
        site = substituted_site(raml.substitutions, type_node, 0, len(text))
        if site is not None:
            # A built-in P7 never reads, which a caller wrote: the view finds
            # every other built-in written alone at its node (docs/16 § 9).
            location, at = site
            base.type_expr_refs = owned(base.type_expr_refs)
            base.type_expr_refs.append(TypeExprRef(line=at.line, column=at.column, location=location, builtin=text))
    return text, None


def _inherited(raml: Raml, item: Node, location: str) -> BaseShape:
    """One member of a multiple-inheritance sequence."""
    if item.kind is not NodeKind.SCALAR:
        raise node_error('a parent type must be a scalar', location, item)
    if item.tag == TAG_INCLUDE:
        raise node_error('!include is not allowed in multiple inheritance', location, item)
    return make_shape(raml, item, item, location)


# -- kind dispatch -----------------------------------------------------------


def attach_kind(raml: Raml, base: BaseShape, kind: str, facets: list[Node], *, from_mapping: bool) -> None:
    """Construct the kind object, then build the declarations it holds into it.

    The kind is attached first, so a declaration facet that fails leaves the
    kind with every child that did build. The failures are raised before the
    other facets are decoded (docs/13 § 1).

    P7 calls this too, to swap the real kind in for an `UnknownShape` once the
    type expression has been resolved (docs/07 § 2).
    """
    base.type = kind
    cls: type[KindBase] = KIND_TO_CLASS.get(kind, UnknownShape)
    shape: Shape
    if cls is UnknownShape:  # noqa: SIM108 - a ternary loses the comment
        # An UnknownShape holds no declarations; the one thing it needs is the
        # flag P7 tells an alias from a subtype by.
        shape = UnknownShape(base, from_mapping=from_mapping)
    else:
        shape = cls(base)
    base.shape = shape
    shape.decode_facets(_decode_declarations(raml, shape, facets, base.location))
    if isinstance(shape, UnionShape):
        _build_member_declarations(raml, shape)
    _check_custom_facet_names(base)


#: The declaration facets a union's members may take from beside `type: A | B`,
#: and the kind whose table defines each (docs/07 § 5).
_MEMBER_DECLARATION_KINDS: Final[dict[str, tuple[str, type[KindBase]]]] = {
    fn.FACET_PROPERTIES: (TYPE_OBJECT, ObjectShape),
    fn.FACET_ITEMS: (TYPE_ARRAY, ArrayShape),
}


def _build_member_declarations(raml: Raml, union: UnionShape) -> None:
    """Build the declarations written beside a union, once, for P9 to hand out.

    A union recognises none of these itself, and which member takes one is not
    known until P9. But they are declarations, so they are built here, with the
    other declaration facets, where P7 still resolves the names they hold. Each
    lands in a holder of the kind that defines the facet.
    """
    location = union.base.location
    pending = union.pending_facets
    holders: dict[str, BaseShape] = {}
    accumulator = Accumulator()
    for index in range(0, len(pending), 2):
        key, value = pending[index], pending[index + 1]
        entry = _MEMBER_DECLARATION_KINDS.get(key.value)
        if entry is None:
            continue
        kind, cls = entry
        holder = BaseShape(
            id=raml.next_id(),
            raml=raml,
            location=location,
            key_pos=key.position,
            value_pos=value.full_position,
            anchor=union.base.anchor,
        )
        holder.type = kind
        holder.shape = cls(holder)
        holders[key.value] = holder
        try:
            _decode_declarations(raml, holder.shape, [key, value], location)
        except RamlError as err:
            accumulator.add(err)
    union.member_declarations = holders
    accumulator.raise_if_any()


def _decode_declarations(raml: Raml, shape: Shape, facets: list[Node], location: str) -> list[Node]:
    """Build the facets whose values are declarations into `shape`; return the rest.

    Each child lands in `shape` as it is built, so one that fails is absent
    and its siblings stay. The failures are raised after all of them. Each
    DECLARATION_FACETS table names the kind's own fields, which is a
    correspondence a checker cannot see; hence `setattr`.
    """
    table = declaration_facets(type(shape))
    if not table:
        return facets
    rest: list[Node] = []
    accumulator = Accumulator()
    for index in range(0, len(facets), 2):
        key, value = facets[index], facets[index + 1]
        spec = table.get(key.value)
        if spec is None:
            rest.append(key)
            rest.append(value)
            continue
        try:
            match spec.kind:
                case 'shape':
                    if value.kind is NodeKind.SEQUENCE:
                        # `items: [Foo, Bar]`. A sequence in a `type:` position is
                        # multiple inheritance, and `items:` *holds* a type
                        # declaration — so reading it that way is tempting and
                        # wrong. The spec's `items` facet says "a reference to an
                        # existing type or an inline type declaration", and a
                        # sequence is neither; go-raml rejects it here too. The
                        # multiply-inheriting form stays available one level in, as
                        # `items: {type: [Foo, Bar]}`.
                        raise node_error(
                            'items must be a reference or an inline type declaration',
                            location,
                            value,
                            info={'facet': key.value},
                        )
                    with raml.target_scope(DomainLocation.TYPE_DECLARATION):
                        setattr(shape, spec.fields[0], make_shape(raml, key, value, location))
                case 'shape_list':
                    value, written = inline_include(raml, value, location)
                    if value.kind is not NodeKind.SEQUENCE:
                        raise node_error('anyOf must be a sequence', written, value)
                    members: list[BaseShape] = []
                    setattr(shape, spec.fields[0], members)
                    for item in value.content:
                        try:
                            with raml.target_scope(DomainLocation.TYPE_DECLARATION):
                                members.append(make_shape(raml, None, item, written))
                        except RamlError as err:
                            accumulator.add(err)
                case 'properties':
                    properties: dict[str, Property] = {}
                    patterns: dict[str, PatternProperty] = {}
                    setattr(shape, spec.fields[0], properties)
                    setattr(shape, spec.fields[1], patterns)
                    make_declarations(raml, value, location, properties, patterns)
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()
    return rest


def _check_custom_facet_names(base: BaseShape) -> None:
    """A `facets:` name may not shadow a built-in one (docs/05 § 5).

    Checked once the kind is known, which is why it is not done where the
    declarations were read.
    """
    if not base.custom_facet_defs:
        return
    specific = TYPE_SPECIFIC_FACETS.get(base.type, frozenset())
    for name in base.custom_facet_defs:
        if name in COMMON_FACETS or name in specific:
            raise node_error(
                'cannot redefine built-in facet',
                base.location,
                # Still a node: `entry._detach_type_expressions` runs after P7.
                cast('Node | None', base.custom_facet_defs[name].base.type_expr),
                info={'facet': name, 'type': base.type},
            )


# -- properties (docs/05 § 4) ----------------------------------------------


def is_pattern_key(name: str) -> bool:
    """A `/regex/` key, including the empty `//` (docs/05 § 4)."""
    return len(name) > 1 and name[0] == '/' and name[-1] == '/'


def make_declarations(
    raml: Raml,
    value_node: Node,
    location: str,
    properties: dict[str, Property],
    patterns: dict[str, PatternProperty],
) -> None:
    """Read a properties declaration into its named and its pattern halves.

    Both maps are the holder's own, filled as each property is built, so one
    that fails leaves the rest. The failures are raised after all of them.
    """
    value_node, location = inline_include(raml, value_node, location)
    if is_null(value_node):
        # `properties:` with nothing under it declares no properties.
        return
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('properties must be a mapping', location, value_node)
    accumulator = Accumulator()
    for key, value in pairs(value_node):
        chomped, _had_optional = chomp_optional(key.value)
        try:
            if is_pattern_key(chomped):
                pattern = make_pattern_property(raml, key, value, location)
                patterns[pattern.pattern.pattern] = pattern
            else:
                prop = make_property(raml, key, value, location)
                properties[prop.name] = prop
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()


def make_parameter_map(
    raml: Raml, value_node: Node, location: str, binding: Binding, declared: dict[str, Parameter]
) -> None:
    """A parameter declaration where a `/regex/` key carries no meaning.

    Headers, query parameters, URI parameters and base-URI parameters. Each one
    joins the flat per-file index, which is what unwrap and validation iterate
    instead of walking the model graph (docs/04 § 5).

    Fill the holder's own map and accumulate per declaration: a failed entry
    is absent, while the good entries on both sides stay attached (docs/11 § 2).

    The binding comes from the caller because only the caller knows it: one
    syntax declares all four, and which one it is is a fact about the map that
    holds them (`Parameter` in docs/05 § 4).
    """
    value_node, location = inline_include(raml, value_node, location)
    if is_null(value_node):
        return
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('parameter declarations must be a mapping', location, value_node)
    location = raml.location_of(value_node, location)
    accumulator = Accumulator()
    # make_property establishes TypeDeclaration for each parameter.
    for key, value in pairs(value_node):
        try:
            prop = make_property(raml, key, value, location)
            declared[prop.name] = Parameter(
                id=raml.next_id(),
                binding=binding,
                declaration=prop,
                key_pos=key.position,
                value_pos=value.position,
            )
            # Indexed under the shape's own file, which provenance may have made
            # a different one from the map's.
            raml.put_typedef(prop.base.location, prop.base)
        except RamlError as error:
            accumulator.add(error)
    accumulator.raise_if_any()


def make_property(raml: Raml, key_node: Node, value_node: Node, location: str) -> Property:
    """One property, header, query parameter, URI parameter or facet declaration.

    The four optionality cases of docs/05 § 4 are all here. The two that
    get mis-implemented: with an explicit `required:` the `?` is part of the
    name, and only one `?` is ever chomped.
    """
    chomped, had_optional = chomp_optional(key_node.value)
    with raml.target_scope(DomainLocation.TYPE_DECLARATION):
        base = make_shape(raml, key_node, value_node, location)
    if base.required is None:
        return Property(name=chomped, base=base, required=not had_optional)
    # An explicit `required:` wins, and the `?` reverts to being part of the name.
    name = key_node.value if had_optional else chomped
    return Property(name=name, base=base, required=base.required.value)


def make_pattern_property(raml: Raml, key_node: Node, value_node: Node, location: str) -> PatternProperty:
    """A `/regex/` property. These are optional by definition, so saying so is an error."""
    chomped, had_optional = chomp_optional(key_node.value)
    with raml.target_scope(DomainLocation.TYPE_DECLARATION):
        base = make_shape(raml, key_node, value_node, location)
    if had_optional or base.required is not None:
        raise node_error(
            "'required' is not supported on a pattern property",
            location,
            key_node,
            info={'property': key_node.value},
        )
    return PatternProperty(pattern=compile_pattern(raml, chomped[1:-1], key_node, location), base=base)


def _parse_data_type(raml: Raml, base: BaseShape, type_node: Node) -> DataTypeFragment:
    """Parse the DataType fragment an `!include` at a type position names.

    `base`, the declaration it is written in, is marked if the fragment failed
    on an earlier include (docs/13 § 1).

    The import is deferred because the recursion is in the language: a type
    may be a file, and a file declares types (docs/02-architecture.md § 2).
    """
    from fastraml.parser.fragments import DataTypeFragment, FragmentKind, parse_included_fragment  # noqa: PLC0415

    location = base.location
    content = content_include(raml, type_node, location, schema=True)
    if content is not None:
        # A file without a header is the declaration, written there
        # (docs/03 § 4.2); not a fragment, so decoded wherever it is included.
        body, written = content
        if body.kind is NodeKind.MAPPING and annotated_scalar_value(body) is not None:
            # A literal include can supply an annotated type/schema scalar,
            # just as it can supply the declaration itself (docs/09 § B4).
            body, _annotations = resolve_annotated_scalar(raml, body, written)
        included = DataTypeFragment(raml, written)
        included.kind = FragmentKind.DATA_TYPE
        included.decode_content(body)
        return included
    target = note_include_ref(raml, type_node, location)
    fragment = parse_included_fragment(raml, target, FragmentKind.DATA_TYPE, type_node, location, referrer=base)
    if not isinstance(fragment, DataTypeFragment):  # pragma: no cover - the kind check guarantees this
        raise node_error('expected a data type fragment', location, type_node)
    return fragment


def _parse_named_example(raml: Raml, base: BaseShape, value_node: Node) -> NamedExample:
    """Parse the NamedExample fragment an `!include` at `examples:` names.

    Deferred, and `base` marked, as in `_parse_data_type`.
    """
    from fastraml.parser.fragments import FragmentKind, NamedExample, parse_included_fragment  # noqa: PLC0415

    location = base.location
    target = note_include_ref(raml, value_node, location)
    fragment = parse_included_fragment(raml, target, FragmentKind.NAMED_EXAMPLE, value_node, location, referrer=base)
    if not isinstance(fragment, NamedExample):  # pragma: no cover - the kind check guarantees this
        raise node_error('expected a named example fragment', location, value_node)
    return fragment
