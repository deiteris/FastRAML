"""Domain extensions — the model behind `(annotation)` keys.

The name comes from AMF by way of go-raml; "annotation" collides with Python's
own vocabulary.

Decoders build and register extensions; P8 (`resolve_domain_extensions`) binds
each to the annotation type it names, after P7. Every extension is appended to
the flat `Raml.domain_extensions` list, so P8 and P10 are single loops rather
than model traversals.

See docs/09-security-and-annotations.md § B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastraml.datanode import make_data_node
from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.parser.references import UnresolvedReferenceError
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import node_error

if TYPE_CHECKING:
    from fastraml.datanode import DataNode
    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.registry import Raml
    from fastraml.yamlnode import Node

    BaseShape = Any

__all__ = [
    'DomainExtension',
    'add_domain_extension',
    'is_annotation_key',
    'resolve_domain_extensions',
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
    #: Where it was applied, for `allowedTargets` enforcement in P10. Captured
    #: at creation time for the same reason the anchor is: the decoder that
    #: finds the key is the only thing that knows.
    target: DomainLocation = DomainLocation.API
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

    # An application an extension document wrote names its annotation type in
    # that document's namespace; the target is still the enclosing site's.
    ctx = raml.current_ctx()
    location = raml.document_location(value_node, location)
    extension = DomainExtension(
        id=raml.next_id(),
        name=name,
        value=make_data_node(raml, key_node, value_node, location),
        location=location,
        key_pos=key_node.position,
        value_pos=value_node.full_position,
        anchor=raml.document_anchor(value_node) or ctx.anchor,
        target=ctx.target,
    )
    raml.domain_extensions.append(extension)
    return extension


def add_domain_extension(
    raml: Raml, into: dict[str, DomainExtension], location: str, key_node: Node, value_node: Node
) -> None:
    """`unmarshal_domain_extension`, attached under its name to `into`."""
    extension = unmarshal_domain_extension(raml, location, key_node, value_node)
    into[extension.name] = extension


def resolve_domain_extensions(raml: Raml) -> None:
    """P8 — bind every application to the annotation type it names.

    One loop over the flat `Raml.domain_extensions` list (docs/09 § B3). Spec
    section Annotations: "All annotations used in an
    API specification MUST be declared in its annotationTypes node", so a name
    that resolves nowhere is an error rather than a shrug.

    Errors accumulate: a document with three undeclared annotations reports
    three. This pass never rewrites a value, only fills `defined_by`.
    """
    accumulator = Accumulator()
    for extension in raml.domain_extensions:
        # An extension built outside a fragment decode — programmatic
        # construction, or a test — has no anchor, so fall back to the index
        # the decoder fills, exactly as P7 does for a shape.
        anchor = extension.anchor or raml.resolver_at(extension.location)
        if anchor is None:
            accumulator.add(_unresolved(extension, 'annotation type not found'))
            continue
        try:
            extension.defined_by = anchor.reference_annotation_type(extension.name)
        except UnresolvedReferenceError as err:
            accumulator.add(_unresolved(extension, err.reason))
    accumulator.raise_if_any()


def _unresolved(extension: DomainExtension, reason: str) -> RamlError:
    return RamlError.new(
        reason,
        extension.location,
        extension.key_pos,
        kind=ErrorKind.RESOLVING,
        info={'annotation': extension.name},
    )
