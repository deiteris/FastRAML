"""`make_shape` — the one way a declaration becomes a shape.

Every property, header, query parameter, body, URI parameter, type declaration
and inline declaration goes through here. The walk is docs/05-type-model.md
section 4: one pass over the mapping, common facets peeled off into the
`BaseShape`, everything else left in a flat `[k0, v0, k1, v1, …]` list for the
kind to read.

Kind dispatch lives here too, so this module imports the kind modules and they
must not import it back. A kind that holds declarations publishes a
`DECLARATION_FACETS` table; this module reads it, builds those children, and
passes them to the constructor (docs/02-architecture.md section 2).

Nothing here resolves. A type expression, a named reference and multiple
inheritance all leave an `UnknownShape` on `Raml.unresolved_shapes` for P7.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastraml.datanode import make_data_node
from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.parser.annotations import is_annotation_key, unmarshal_domain_extension
from fastraml.parser.facets import compile_pattern, make_bool_facet, make_string_facet, scalar_str
from fastraml.parser.includes import note_include_ref
from fastraml.types.base import (
    BUILTIN_TYPES,
    TYPE_ANY,
    TYPE_COMPOSITE,
    TYPE_JSON,
    TYPE_STRING,
    BaseShape,
    Binding,
    Parameter,
    PatternProperty,
    Property,
    Shape,
    declaration_facets,
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
from fastraml.types.xml import decode_xml_serialization
from fastraml.yamlnode import TAG_INCLUDE, TAG_NULL, TAG_STR, NodeKind, is_null, node_error, pairs

if TYPE_CHECKING:
    from typing import Any

    from fastraml.parser.fragments import DataTypeFragment, NamedExample
    from fastraml.registry import Raml
    from fastraml.yamlnode import Node

__all__ = [
    'COMMON_FACETS',
    'TYPE_SPECIFIC_FACETS',
    'attach_kind',
    'chomp_optional',
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
KIND_TO_CLASS: Final[dict[str, type[Shape]]] = {
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

#: Built-in facets every kind has. A `facets:` declaration may not shadow one
#: (docs/05 section 7). `strict` and `value` are example-level keys, not type
#: facets, and are deliberately absent.
COMMON_FACETS: Final = frozenset(
    {
        'type',
        'schema',
        'facets',
        'example',
        'examples',
        'default',
        'description',
        'displayName',
        'required',
        'enum',
        'allowedTargets',
    }
)

#: Built-in facets of one kind, which a `facets:` declaration on that kind may
#: not shadow either.
TYPE_SPECIFIC_FACETS: Final[dict[str, frozenset[str]]] = {
    'object': frozenset(
        {
            'properties',
            'additionalProperties',
            'minProperties',
            'maxProperties',
            'discriminator',
            'discriminatorValue',
        }
    ),
    'array': frozenset({'items', 'minItems', 'maxItems', 'uniqueItems'}),
    'string': frozenset({'pattern', 'minLength', 'maxLength'}),
    'integer': frozenset({'minimum', 'maximum', 'multipleOf'}),
    'number': frozenset({'minimum', 'maximum', 'multipleOf'}),
    'file': frozenset({'fileTypes'}),
}


def make_shape(
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    default_type: str = TYPE_STRING,
) -> BaseShape:
    """Build one declaration. `default_type` applies when nothing else settles it."""
    scope = raml.scope_for(value_node)
    if scope is None:
        return _make_shape(raml, key_node, value_node, raml.location_of(value_node, location), default_type)
    # A node produced by parameter substitution, or grafted from a trait or a
    # resource type, resolves its unqualified names in the namespace recorded
    # for it — not the applying document's (docs/08 section 6.3). Pushed around
    # the whole build, so nested facets inherit it.
    raml.push_ctx(scope)
    try:
        return _make_shape(raml, key_node, value_node, raml.location_of(value_node, location), default_type)
    finally:
        raml.pop_ctx()


def _make_shape(
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    default_type: str,
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
    )
    raml.put_source_info(base.id, key_node, value_node)

    type_node, facets = _decode(raml, base, value_node)
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
    # sequence one has nothing to narrow with. That is the whole of docs/06
    # section 3.1, and only an UnknownShape ever reads it.
    attach_kind(raml, base, kind, facets, from_mapping=value_node.kind is NodeKind.MAPPING)
    raml.put_shape(base)
    if isinstance(base.shape, UnknownShape):
        # Invariant I4: P7 drains this worklist and swaps in the real kind.
        raml.unresolved_shapes.append(base)
    return base


def unmarshal_types(raml: Raml, node: Node, location: str, *, is_annotation: bool = False) -> dict[str, BaseShape]:
    """Decode a `types:`, `schemas:` or `annotationTypes:` mapping.

    Per name: reject a built-in name, reject a duplicate in the same map, build
    the shape, register it under the file, and append it to the flat per-file
    index that unwrap and validation iterate (docs/04 section 5.1).

    Errors accumulate, so one bad declaration does not hide the rest.
    """
    if is_null(node):
        # `types:` with nothing under it. RAML uses an empty value widely.
        return {}
    if node.kind is not NodeKind.MAPPING:
        raise node_error('type declarations must be a mapping', location, node)

    declared: dict[str, BaseShape] = {}
    accumulator = Accumulator()
    # An annotation written on one of these declarations targets the
    # declaration, not the file that holds it (docs/09 section B5).
    target = DomainLocation.ANNOTATION_TYPE if is_annotation else DomainLocation.TYPE_DECLARATION
    with raml.target_scope(target):
        for key, value in pairs(node):
            name = key.value
            try:
                if name in BUILTIN_TYPES:
                    raise node_error('cannot redefine a built-in type', location, key, info={'type': name})
                if name in declared:
                    raise node_error('duplicate type name', location, key, info={'type': name})
                base = make_shape(raml, key, value, location)
                base.is_annotation_type = is_annotation
                declared[name] = base
                if is_annotation:
                    raml.put_annotation_type(name, location, base)
                else:
                    raml.put_type(name, location, base)
                raml.put_typedef(location, base)
            except RamlError as err:
                accumulator.add(err)
    accumulator.raise_if_any()
    return declared


def make_body_shape(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> BaseShape:
    """`make_shape` for a `body:` node, whose default type is `any`.

    Spec section Determine Default Types: "The default type `any` is applied to
    any `body` node that does not contain `properties`, `type`, or `schema`."
    """
    return make_shape(raml, key_node, value_node, location, TYPE_ANY)


# -- the section 4 walk ------------------------------------------------------


def _decode(  # noqa: PLR0912 - one pass over the sixteen-row table of docs/05 section 4
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
            case 'type' | 'schema':
                if type_node is not None:
                    raise node_error('`type` and `schema` are mutually exclusive', location, key)
                type_node = value
            case 'displayName':
                base.display_name = make_string_facet(raml, key, value, location)
            case 'description':
                base.description = make_string_facet(raml, key, value, location)
            case 'required':
                base.required = make_bool_facet(raml, key, value, location)
            case 'facets':
                _decode_custom_facet_defs(raml, base, value)
            case 'example':
                _decode_example(raml, base, value)
            case 'examples':
                _decode_examples(raml, base, value)
            case 'default':
                base.default = make_data_node(raml, key, value, location)
            case 'enum':
                base.enum = _decode_enum(raml, value, location)
            case 'xml':
                base.xml = decode_xml_serialization(raml, value, location)
            case 'allowedTargets':
                base.allowed_targets = _decode_allowed_targets(value, location)
            case name if is_annotation_key(name):
                extension = unmarshal_domain_extension(raml, location, key, value)
                base.annotations[extension.name] = extension
            case _:
                facets.append(key)
                facets.append(value)
    return type_node, facets


def _decode_allowed_targets(value_node: Node, location: str) -> list[DomainLocation]:
    """`allowedTargets:` — one target name or a sequence of them (docs/09 § B5).

    The result is a list either way, but an *absent* facet stays `None` on the
    base: absent means any target is allowed and empty means none is, and P10
    has to tell them apart.
    """
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
    accumulator.raise_if_any()
    return targets


def _decode_enum(raml: Raml, value_node: Node, location: str) -> list:
    if value_node.kind is not NodeKind.SEQUENCE:
        raise node_error('enum must be a sequence', location, value_node)
    return [make_data_node(raml, None, item, location) for item in value_node.content]


def _decode_example(raml: Raml, base: BaseShape, value_node: Node) -> None:
    if base.examples is not None:
        raise node_error('example and examples cannot be defined together', base.location, value_node)
    base.example = make_example(raml, value_node, '', base.location)


def _decode_examples(raml: Raml, base: BaseShape, value_node: Node) -> None:
    if base.example is not None:
        raise node_error('example and examples cannot be defined together', base.location, value_node)
    if value_node.tag == TAG_INCLUDE:
        base.examples = Examples(
            location=base.location,
            position=value_node.full_position,
            link=_parse_named_example(raml, value_node, base.location),
        )
        return
    if is_null(value_node):
        return
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('examples must be a mapping', base.location, value_node)
    values = {key.value: make_example(raml, value, key.value, base.location) for key, value in pairs(value_node)}
    base.examples = Examples(location=base.location, position=value_node.full_position, values=values)


def _decode_custom_facet_defs(raml: Raml, base: BaseShape, value_node: Node) -> None:
    """`facets:` — a properties declaration, so it reuses `make_property`."""
    if is_null(value_node):
        return
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('facets must be a mapping', base.location, value_node)
    for key, value in pairs(value_node):
        if key.value.startswith('('):
            # Otherwise a facet name would be ambiguous with an annotation.
            raise node_error("facet name must not begin with '('", base.location, key, info={'facet': key.value})
        prop = make_property(raml, key, value, base.location)
        base.custom_facet_defs[prop.name] = prop


# -- section 4.1: what type is this? -----------------------------------------


def _decode_type_node(
    raml: Raml,
    base: BaseShape,
    type_node: Node,
    facets: list[Node],
    default_type: str,
) -> tuple[str, Shape | None]:
    """Read the `type:` node. Returns the kind, and a shape when it built one."""
    location = base.location
    if type_node.kind is NodeKind.SEQUENCE:
        base.inherits = [_inherited(raml, item, location) for item in type_node.content]
        return TYPE_COMPOSITE, None
    if type_node.kind is not NodeKind.SCALAR:
        raise node_error('type must be a string or a sequence', location, type_node)

    # Retained whole, so P7 can report a column inside the expression and tooling
    # can offer go-to-definition on each name within it.
    base.type_expr = type_node
    note_include_ref(raml, type_node, location)

    if type_node.tag == TAG_INCLUDE:
        base.link = _parse_data_type(raml, type_node, location)
        return '', None
    if type_node.tag == TAG_NULL:
        # `default_type`, not `TYPE_STRING`: for a `type:` written with no value
        # the two are the same, but a `body:` declared with nothing under it is
        # `any` — spec section Determine Default Types, "the default type `any`
        # is applied to any body node that does not contain properties, type, or
        # schema".
        return default_type, None
    if type_node.tag != TAG_STR:
        raise node_error('type must be a string', location, type_node)

    text = type_node.value
    if not text:
        return identify_shape_type(facets, default_type, location), None
    if text[0] == '{':
        # An inline JSON Schema. `decode_json_schema` has already wrapped an
        # external .json file into this same form (docs/04 section 5.2).
        return TYPE_JSON, JsonShape(base, raw=text)
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
    """Construct the kind object, giving it any children it holds.

    P7 calls this too, to swap the real kind in for an `UnknownShape` once the
    type expression has been resolved (docs/07 section 1.1).
    """
    base.type = kind
    cls: type[Shape] = KIND_TO_CLASS.get(kind, UnknownShape)
    rest, built = _split_declarations(raml, cls, facets, base.location)
    shape: Shape
    if cls is UnknownShape:  # noqa: SIM108 - a ternary loses both comments and widens the `type: ignore`
        # An UnknownShape holds no declarations, so `built` is empty; the one
        # thing it needs is the flag P7 tells an alias from a subtype by.
        shape = UnknownShape(base, from_mapping=from_mapping)
    else:
        # Each DECLARATION_FACETS table names its own constructor keywords, which
        # is a correspondence a checker cannot see.
        shape = cls(base, **built)  # type: ignore[call-arg]
    base.shape = shape
    shape.decode_facets(rest)
    _check_custom_facet_names(base)


def _split_declarations(
    raml: Raml, cls: type[Shape], facets: list[Node], location: str
) -> tuple[list[Node], dict[str, Any]]:
    """Build the facets whose values are declarations; return the rest."""
    table = declaration_facets(cls)
    if not table:
        return facets, {}
    rest: list[Node] = []
    built: dict[str, Any] = {}
    for index in range(0, len(facets), 2):
        key, value = facets[index], facets[index + 1]
        spec = table.get(key.value)
        if spec is None:
            rest.append(key)
            rest.append(value)
            continue
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
                built[spec.fields[0]] = make_shape(raml, key, value, location)
            case 'shape_list':
                if value.kind is not NodeKind.SEQUENCE:
                    raise node_error('anyOf must be a sequence', location, value)
                built[spec.fields[0]] = [make_shape(raml, None, item, location) for item in value.content]
            case 'properties':
                properties, patterns = make_declarations(raml, value, location)
                built[spec.fields[0]] = properties
                built[spec.fields[1]] = patterns
    return rest, built


def _check_custom_facet_names(base: BaseShape) -> None:
    """A `facets:` name may not shadow a built-in one (docs/05 section 7).

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
                base.custom_facet_defs[name].base.type_expr,
                info={'facet': name, 'type': base.type},
            )


# -- properties (docs/05 sections 5 and 5.1) ---------------------------------


def chomp_optional(name: str) -> tuple[str, bool]:
    """Strip **one** trailing `?`, reporting whether there was one.

    Exactly one: `name??` is the optional property `name?` (docs/05 § 5, rule 4).
    """
    if name.endswith('?'):
        return name[:-1], True
    return name, False


def is_pattern_key(name: str) -> bool:
    """A `/regex/` key, including the empty `//` (docs/05 section 5.1)."""
    return len(name) > 1 and name[0] == '/' and name[-1] == '/'


def make_declarations(
    raml: Raml, value_node: Node, location: str
) -> tuple[dict[str, Property], dict[str, PatternProperty]]:
    """Read a properties declaration into its named and its pattern halves."""
    if is_null(value_node):
        # `properties:` with nothing under it declares no properties.
        return {}, {}
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('properties must be a mapping', location, value_node)
    properties: dict[str, Property] = {}
    patterns: dict[str, PatternProperty] = {}
    for key, value in pairs(value_node):
        chomped, _had_optional = chomp_optional(key.value)
        if is_pattern_key(chomped):
            pattern = make_pattern_property(raml, key, value, location)
            patterns[pattern.pattern.pattern] = pattern
        else:
            prop = make_property(raml, key, value, location)
            properties[prop.name] = prop
    return properties, patterns


def make_parameter_map(raml: Raml, value_node: Node, location: str, binding: Binding) -> dict[str, Parameter]:
    """A parameter declaration where a `/regex/` key carries no meaning.

    Headers, query parameters, URI parameters and base-URI parameters. Each one
    joins the flat per-file index, which is what unwrap and validation iterate
    instead of walking the model graph (docs/04 section 5.1).

    The binding comes from the caller because only the caller knows it: one
    syntax declares all four, and which one it is is a fact about the map that
    holds them (`Parameter` in docs/05 section 5).
    """
    if is_null(value_node):
        return {}
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('parameter declarations must be a mapping', location, value_node)
    location = raml.location_of(value_node, location)
    declared: dict[str, Parameter] = {}
    # Each parameter is a type declaration, whatever holds the map.
    with raml.target_scope(DomainLocation.TYPE_DECLARATION):
        for key, value in pairs(value_node):
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
    return declared


def make_property(raml: Raml, key_node: Node, value_node: Node, location: str) -> Property:
    """One property, header, query parameter, URI parameter or facet declaration.

    The four optionality cases of docs/05 section 5 are all here. The two that
    get mis-implemented: with an explicit `required:` the `?` is part of the
    name, and only one `?` is ever chomped.
    """
    chomped, had_optional = chomp_optional(key_node.value)
    base = make_shape(raml, key_node, value_node, location)
    if base.required is None:
        return Property(name=chomped, base=base, required=not had_optional)
    # An explicit `required:` wins, and the `?` reverts to being part of the name.
    name = key_node.value if had_optional else chomped
    return Property(name=name, base=base, required=base.required.value)


def make_pattern_property(raml: Raml, key_node: Node, value_node: Node, location: str) -> PatternProperty:
    """A `/regex/` property. These are optional by definition, so saying so is an error."""
    chomped, had_optional = chomp_optional(key_node.value)
    base = make_shape(raml, key_node, value_node, location)
    if had_optional or base.required is not None:
        raise node_error(
            "'required' is not supported on a pattern property",
            location,
            key_node,
            info={'property': key_node.value},
        )
    return PatternProperty(pattern=compile_pattern(raml, chomped[1:-1], key_node, location), base=base)


def _parse_data_type(raml: Raml, type_node: Node, location: str) -> DataTypeFragment:
    """Parse the DataType fragment an `!include` at a type position names.

    The import is deferred because the recursion is in the language, not in the
    module layout: a type may be a file, and a file declares types. Every other
    ordering of these two modules is the same cycle wearing a different hat, so
    this is the one place `types/` reaches for `parser.fragments`.
    See docs/02-architecture.md section 2.
    """
    from fastraml.parser.fragments import DataTypeFragment, FragmentKind, parse_fragment  # noqa: PLC0415

    fragment = parse_fragment(raml, note_include_ref(raml, type_node, location), FragmentKind.DATA_TYPE)
    if not isinstance(fragment, DataTypeFragment):  # pragma: no cover - the kind check guarantees this
        raise node_error('expected a data type fragment', location, type_node)
    return fragment


def _parse_named_example(raml: Raml, value_node: Node, location: str) -> NamedExample:
    """Parse the NamedExample fragment an `!include` at `examples:` names.

    Deferred for the same reason as `_parse_data_type`.
    """
    from fastraml.parser.fragments import FragmentKind, NamedExample, parse_fragment  # noqa: PLC0415

    fragment = parse_fragment(raml, note_include_ref(raml, value_node, location), FragmentKind.NAMED_EXAMPLE)
    if not isinstance(fragment, NamedExample):  # pragma: no cover - the kind check guarantees this
        raise node_error('expected a named example fragment', location, value_node)
    return fragment
