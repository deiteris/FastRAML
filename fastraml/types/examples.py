"""`example:` and `examples:`.

Spec section Single Example allows two forms, and they are ambiguous:

    example:                # form A: the example IS this object
      name: Bob
    example:                # form B: a wrapper carrying metadata
      value: {name: Bob}
      strict: false
      (pii): true

The rule is that a mapping containing a `value` key is form B, and anything else
is form A. A type whose example value has a property called `value` must use the
wrapper form explicitly. See docs/05-type-model.md section 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fastraml.datanode import make_data_node
from fastraml.domains import DomainLocation
from fastraml.parser.annotations import is_annotation_key, unmarshal_domain_extension
from fastraml.parser.facets import make_bool_facet, make_string_facet
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import NodeKind, node_error, pairs

if TYPE_CHECKING:
    from fastraml.datanode import DataNode
    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.fragments import NamedExample
    from fastraml.registry import Raml
    from fastraml.types.base import ScalarFacet
    from fastraml.yamlnode import Node

__all__ = [
    'Example',
    'Examples',
    'make_example',
]

#: The key whose presence selects form B.
EXAMPLE_VALUE: Final = 'value'


@dataclass(slots=True, eq=False)
class Example:
    """One example value, with whatever metadata form B carried."""

    id: int
    name: str
    location: str
    data: DataNode | None = None
    display_name: ScalarFacet[str] | None = None
    description: ScalarFacet[str] | None = None
    strict: ScalarFacet[bool] | None = None
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'Example({self.name!r})'


@dataclass(slots=True, eq=False)
class Examples:
    """The `examples:` facet: named examples, or a link to a NamedExample."""

    location: str
    position: Position = UNKNOWN
    #: Declaration order is preserved, as everywhere the model is exposed.
    values: dict[str, Example] = field(default_factory=dict)
    #: Set instead of `values` when `examples:` was an `!include`.
    link: NamedExample | None = None

    def __repr__(self) -> str:
        return f'Examples({list(self.values)!r})' if self.link is None else 'Examples(link)'

    def entries(self) -> dict[str, Example]:
        """The examples this facet holds, following an `!include` if there is one.

        Every consumer wants both forms. Reading `values` directly is how a
        linked NamedExample went unvalidated: the map is empty and the examples
        are one hop away on the fragment.
        """
        if self.link is not None:
            return self.link.examples
        return self.values


def make_example(raml: Raml, value_node: Node, name: str, location: str) -> Example:
    """Build one example, choosing between the two forms by the `value` key."""
    example = Example(
        id=raml.next_id(),
        name=name,
        location=location,
        key_pos=value_node.full_position,
        value_pos=value_node.full_position,
    )
    # An annotation inside an example targets the example, not the declaration
    # the example belongs to (docs/09 section B5).
    with raml.target_scope(DomainLocation.EXAMPLE):
        if value_node.kind is NodeKind.MAPPING and _has_value_key(value_node):
            _fill_from_wrapper(raml, example, value_node, location)
        else:
            example.data = make_data_node(raml, None, value_node, location)
    return example


def _has_value_key(value_node: Node) -> bool:
    return any(key.value == EXAMPLE_VALUE for key, _ in pairs(value_node))


def _fill_from_wrapper(raml: Raml, example: Example, value_node: Node, location: str) -> None:
    """Form B: read `value` plus the metadata keys and any annotations."""
    for key, value in pairs(value_node):
        match key.value:
            case 'value':
                example.data = make_data_node(raml, key, value, location)
            case 'strict':
                example.strict = make_bool_facet(raml, key, value, location)
            case 'displayName':
                example.display_name = make_string_facet(raml, key, value, location)
            case 'description':
                example.description = make_string_facet(raml, key, value, location)
            case name if is_annotation_key(name):
                extension = unmarshal_domain_extension(raml, location, key, value)
                example.annotations[extension.name] = extension
            case _:
                raise node_error('unknown field', location, key, info={'field': key.value})
