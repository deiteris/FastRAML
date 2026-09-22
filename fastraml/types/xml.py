"""The `xml:` facet.

Parsed, positioned, and retained for consumers and projections. The parser does
not apply XML wire serialization itself. See docs/05-type-model.md section 5.

Unknown keys inside `xml:` are an error rather than being ignored, so `wraped:`
is caught where it is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastraml.parser.facets import make_bool_facet, make_string_facet
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import NodeKind, node_error, pairs

if TYPE_CHECKING:
    from fastraml.registry import Raml
    from fastraml.types.base import ScalarFacet
    from fastraml.yamlnode import Node

__all__ = [
    'XmlSerialization',
    'decode_xml_serialization',
]


@dataclass(slots=True, eq=False)
class XmlSerialization:
    """How a declaration is written when the payload is XML."""

    location: str
    position: Position = UNKNOWN
    attribute: ScalarFacet[bool] | None = None
    wrapped: ScalarFacet[bool] | None = None
    name: ScalarFacet[str] | None = None
    namespace: ScalarFacet[str] | None = None
    prefix: ScalarFacet[str] | None = None

    def __repr__(self) -> str:
        return f'XmlSerialization(name={None if self.name is None else self.name.value!r})'


def decode_xml_serialization(raml: Raml, value_node: Node, location: str) -> XmlSerialization:
    """Build the record from an `xml:` value node."""
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('xml must be a mapping', location, value_node)

    xml = XmlSerialization(location=location, position=value_node.full_position)
    for key, value in pairs(value_node):
        match key.value:
            case 'attribute':
                xml.attribute = make_bool_facet(raml, key, value, location)
            case 'wrapped':
                xml.wrapped = make_bool_facet(raml, key, value, location)
            case 'name':
                xml.name = make_string_facet(raml, key, value, location)
            case 'namespace':
                xml.namespace = make_string_facet(raml, key, value, location)
            case 'prefix':
                xml.prefix = make_string_facet(raml, key, value, location)
            case _:
                raise node_error('unknown xml property', location, key, info={'property': key.value})
    return xml
