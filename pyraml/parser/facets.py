"""Scalar facets, and the annotated-scalar form.

Spec section Annotating Scalar-valued Nodes lets *any* scalar-valued node be
written as a map with a `value` key, so that annotations can be attached:

    baseUri:
      value: http://www.example.com/api
      (redirectable): true

Because every scalar facet in the language is built by `make_scalar_facet`, that
form works at all thirty-odd nodes the spec lists without a line of per-facet
code. The same is true of `!include` at a facet position.

See docs/03-yaml-and-io.md section 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from pyraml.parser.annotations import DomainExtension, is_annotation_key, unmarshal_domain_extension
from pyraml.parser.includes import IncludeInfo, resolve_include
from pyraml.positions import UNKNOWN, Position
from pyraml.yamlnode import TAG_NULL, Node, NodeKind, node_error, pairs

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyraml.registry import Raml

__all__ = [
    'ScalarFacet',
    'make_scalar_facet',
    'make_seq_facet',
    'make_string_facet',
    'resolve_annotated_scalar',
    'scalar_str',
]

#: The key that carries the value in the annotated-scalar form.
FACET_VALUE: Final = 'value'


@dataclass(slots=True, eq=False)
class ScalarFacet[T]:
    """A decoded scalar facet: its value, where it came from, and its annotations."""

    value: T
    location: str
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: Set when the value arrived through `!include`.
    include: IncludeInfo | None = None
    #: Annotations collected from the annotated-scalar form.
    annotations: dict[str, DomainExtension] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f'ScalarFacet({self.value!r})'


def scalar_str(node: Node, location: str) -> str:
    """A scalar node as text. An empty (null) node reads as `''`.

    The literal text is used whatever the resolved tag: `version: 1` is the
    string `'1'`, which is what the reference implementation's YAML decoder does
    for a string destination.
    """
    if node.kind is not NodeKind.SCALAR:
        raise node_error('expected a scalar value', location, node)
    if node.tag == TAG_NULL:
        return ''
    return node.value


def resolve_annotated_scalar(raml: Raml, node: Node, location: str) -> tuple[Node, dict[str, DomainExtension]]:
    """Unwrap the annotated-scalar form, returning the value node and any annotations.

    A scalar is returned unchanged. A mapping must carry `value`, may carry
    `(annotation)` keys, and may carry nothing else.
    """
    if node.kind is NodeKind.SCALAR:
        return node, {}
    if node.kind is not NodeKind.MAPPING:
        raise node_error('expected scalar or mapping node', location, node)

    value_node: Node | None = None
    extensions: dict[str, DomainExtension] = {}
    for key, value in pairs(node):
        if key.value == FACET_VALUE:
            value_node = value
        elif is_annotation_key(key.value):
            extension = unmarshal_domain_extension(raml, location, key, value)
            extensions[extension.name] = extension
        else:
            raise node_error('unknown field in annotated scalar', location, key, info={'field': key.value})

    if value_node is None:
        raise node_error('missing value key in annotated scalar', location, node)
    return value_node, extensions


def make_scalar_facet[T](
    raml: Raml,
    key_node: Node | None,
    value_node: Node,
    location: str,
    convert: Callable[[Node, str], T],
) -> ScalarFacet[T]:
    """Build one scalar facet: resolve an include, unwrap annotations, convert.

    `key_node` is `None` for a sequence item, which has no key of its own.
    """
    target, resolved = resolve_include(raml, value_node, location)
    resolved, extensions = resolve_annotated_scalar(raml, resolved, location)
    return ScalarFacet(
        value=convert(resolved, location),
        location=location,
        key_pos=key_node.position if key_node is not None else UNKNOWN,
        value_pos=value_node.full_position,
        include=IncludeInfo(path=value_node.value, abs_uri=target) if target else None,
        annotations=extensions,
    )


def make_string_facet(raml: Raml, key_node: Node | None, value_node: Node, location: str) -> ScalarFacet[str]:
    """`make_scalar_facet` for the common case of a string-valued facet."""
    return make_scalar_facet(raml, key_node, value_node, location, scalar_str)


def make_seq_facet[T](raml: Raml, value_node: Node, location: str, convert: Callable[[Node, str], T]) -> ScalarFacet[T]:
    """A facet built from a sequence item, which has no key node."""
    return make_scalar_facet(raml, None, value_node, location, convert)
