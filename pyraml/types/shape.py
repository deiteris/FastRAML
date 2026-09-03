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

from pyraml.datanode import make_data_node
from pyraml.parser.annotations import is_annotation_key, unmarshal_domain_extension
from pyraml.parser.facets import compile_pattern, make_bool_facet, make_string_facet
from pyraml.parser.includes import note_include_ref
from pyraml.types.base import (
    TYPE_ANY,
    TYPE_COMPOSITE,
    TYPE_JSON,
    TYPE_STRING,
    BaseShape,
    PatternProperty,
    Property,
    Shape,
    declaration_facets,
)
from pyraml.types.complex_ import (
    ArrayShape,
    JsonShape,
    ObjectShape,
    UnionShape,
    UnknownShape,
)
from pyraml.types.examples import Examples, make_example
from pyraml.types.inference import identify_shape_type
from pyraml.types.scalars import (
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
from pyraml.types.xml import decode_xml_serialization
from pyraml.yamlnode import TAG_INCLUDE, TAG_NULL, TAG_STR, NodeKind, node_error, pairs

if TYPE_CHECKING:
    from typing import Any

    from pyraml.parser.fragments import DataTypeFragment, NamedExample
    from pyraml.registry import Raml
    from pyraml.yamlnode import Node

__all__ = [
    'COMMON_FACETS',
    'TYPE_SPECIFIC_FACETS',
    'chomp_optional',
    'make_body_shape',
    'make_declarations',
    'make_pattern_property',
    'make_property',
    'make_shape',
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

    _attach_kind(raml, base, kind, facets)
    raml.put_shape(base)
    if isinstance(base.shape, UnknownShape):
        # Invariant I4: P7 drains this worklist and swaps in the real kind.
        raml.unresolved_shapes.append(base)
    return base


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
    for key, value in pairs(value_node):
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
                # An annotation-type facet; docs/09 owns it and Phase 7 decodes
                # it. Consumed here so it does not become a custom facet value.
                pass
            case name if is_annotation_key(name):
                extension = unmarshal_domain_extension(raml, location, key, value)
                base.annotations[extension.name] = extension
            case _:
                facets += (key, value)
    return type_node, facets


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
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('examples must be a mapping', base.location, value_node)
    values = {key.value: make_example(raml, value, key.value, base.location) for key, value in pairs(value_node)}
    base.examples = Examples(location=base.location, position=value_node.full_position, values=values)


def _decode_custom_facet_defs(raml: Raml, base: BaseShape, value_node: Node) -> None:
    """`facets:` — a properties declaration, so it reuses `make_property`."""
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
        return TYPE_STRING, None
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


def _attach_kind(raml: Raml, base: BaseShape, kind: str, facets: list[Node]) -> None:
    """Construct the kind object, giving it any children it holds."""
    base.type = kind
    cls: type[Shape] = KIND_TO_CLASS.get(kind, UnknownShape)
    rest, built = _split_declarations(raml, cls, facets, base.location)
    # Each DECLARATION_FACETS table names its own constructor keywords, which is
    # a correspondence a checker cannot see.
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
            rest += (key, value)
            continue
        match spec.kind:
            case 'shape':
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
    from pyraml.parser.fragments import DataTypeFragment, FragmentKind, parse_fragment  # noqa: PLC0415

    fragment = parse_fragment(raml, note_include_ref(raml, type_node, location), FragmentKind.DATA_TYPE)
    if not isinstance(fragment, DataTypeFragment):  # pragma: no cover - the kind check guarantees this
        raise node_error('expected a data type fragment', location, type_node)
    return fragment


def _parse_named_example(raml: Raml, value_node: Node, location: str) -> NamedExample:
    """Parse the NamedExample fragment an `!include` at `examples:` names.

    Deferred for the same reason as `_parse_data_type`.
    """
    from pyraml.parser.fragments import FragmentKind, NamedExample, parse_fragment  # noqa: PLC0415

    fragment = parse_fragment(raml, note_include_ref(raml, value_node, location), FragmentKind.NAMED_EXAMPLE)
    if not isinstance(fragment, NamedExample):  # pragma: no cover - the kind check guarantees this
        raise node_error('expected a named example fragment', location, value_node)
    return fragment
