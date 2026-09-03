"""Domain extensions — the model behind `(annotation)` keys.

The internal name comes from AMF, by way of go-raml, and is kept because
"annotation" collides with Python's own vocabulary in a heavily typed codebase.

Phase 1 builds and registers extensions but does not resolve them: binding a
name to its declaration needs annotation types, which arrive with the type
system. Every extension is appended to the flat `Raml.domain_extensions` list,
which is what lets the later resolution and validation passes be single loops
rather than a traversal of the model.

See docs/09-security-and-annotations.md part B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyraml.datanode import make_data_node
from pyraml.positions import UNKNOWN, Position
from pyraml.yamlnode import node_error

if TYPE_CHECKING:
    from pyraml.datanode import DataNode
    from pyraml.parser.fragments import ReferenceResolver
    from pyraml.registry import Raml
    from pyraml.yamlnode import Node

    BaseShape = Any

__all__ = [
    'DomainExtension',
    'is_annotation_key',
    'unmarshal_domain_extension',
]


@dataclass(slots=True, eq=False)
class DomainExtension:
    """One application of an annotation, e.g. `(oas-deprecated): true`."""

    id: int
    name: str
    value: DataNode
    location: str
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: The scope the annotation *name* resolves in, captured at creation time.
    anchor: ReferenceResolver | None = None
    #: The annotation type this application was bound to. Filled by P8.
    defined_by: BaseShape | None = None

    def __repr__(self) -> str:
        return f'DomainExtension({self.name!r})'


def is_annotation_key(name: str) -> bool:
    """Whether a mapping key is an annotation application: `(name)`."""
    return len(name) > 1 and name[0] == '(' and name[-1] == ')'


def unmarshal_domain_extension(raml: Raml, location: str, key_node: Node, value_node: Node) -> DomainExtension:
    """Build one extension from a `(name): value` pair and register it.

    The caller attaches the returned object wherever the annotation was written;
    registration in `Raml.domain_extensions` has already happened.
    """
    name = key_node.value[1:-1]
    if not name:
        raise node_error('annotation name must not be empty', location, key_node)

    extension = DomainExtension(
        id=raml.next_id(),
        name=name,
        value=make_data_node(raml, key_node, value_node, location),
        location=location,
        key_pos=key_node.position,
        value_pos=value_node.full_position,
        anchor=raml.current_ctx().anchor,
    )
    raml.domain_extensions.append(extension)
    return extension
