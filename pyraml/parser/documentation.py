"""`DocumentationItem` — one entry of the API's `documentation:` node.

The item is also a fragment kind of its own, so the same decoder serves
`documentation: [{title: …, content: …}]` and
`documentation: [!include docs/intro.raml]`.

This module knows nothing about fragments; the `!include`-of-a-fragment branch
lives in `pyraml.parser.fragments`, which is what keeps the import graph acyclic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pyraml.parser.annotations import DomainExtension, is_annotation_key, unmarshal_domain_extension
from pyraml.parser.facets import ScalarFacet, make_string_facet
from pyraml.positions import UNKNOWN, Position
from pyraml.yamlnode import NodeKind, node_error, pairs

if TYPE_CHECKING:
    from pyraml.registry import Raml
    from pyraml.yamlnode import Node

    DocumentationItemFragment = Any

__all__ = [
    'DocumentationItem',
    'decode_documentation_item',
]

FACET_TITLE: Final = 'title'
FACET_CONTENT: Final = 'content'


@dataclass(slots=True, eq=False)
class DocumentationItem:
    """A title and a body of markdown, both required and both non-empty."""

    id: int
    location: str
    title: ScalarFacet[str] | None = None
    content: ScalarFacet[str] | None = None
    #: Set when the item came from a `DocumentationItem` fragment.
    link: DocumentationItemFragment | None = None
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        title = self.title.value if self.title is not None else None
        return f'DocumentationItem({title!r})'


def decode_documentation_item(raml: Raml, node: Node, location: str) -> DocumentationItem:
    """Decode a `{title, content}` mapping into an item.

    `content` is usually `!include`d from a markdown file; the include machinery
    handles that, since a non-YAML include produces a string scalar.
    """
    if node.kind is not NodeKind.MAPPING:
        raise node_error('documentation item must be a mapping node', location, node)

    item = DocumentationItem(id=raml.next_id(), location=location, key_pos=node.position, value_pos=node.full_position)
    for key, value in pairs(node):
        if key.value == FACET_TITLE:
            item.title = _required_text(raml, key, value, location)
        elif key.value == FACET_CONTENT:
            item.content = _required_text(raml, key, value, location)
        elif is_annotation_key(key.value):
            extension = unmarshal_domain_extension(raml, location, key, value)
            item.annotations[extension.name] = extension
        else:
            raise node_error('unknown field', location, key, info={'field': key.value})

    if item.title is None:
        raise node_error('title is required', location, node)
    if item.content is None:
        raise node_error('content is required', location, node)
    return item


def _required_text(raml: Raml, key: Node, value: Node, location: str) -> ScalarFacet[str]:
    facet = make_string_facet(raml, key, value, location)
    if not facet.value:
        # The message stays constant and the field travels in `info`, so tests
        # and log grouping key on one string. See docs/11-diagnostics.md § 6.
        raise node_error('value must not be empty', location, key, info={'field': key.value})
    return facet
