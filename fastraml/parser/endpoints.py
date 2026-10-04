"""The endpoint model: what stage 2 produces and what a consumer reads.

Five records, all read-oriented and all slotted. `Request` and `Response` exist
as objects rather than as loose fields on `Operation` because the spec gives them
the same node vocabulary — "the syntax and semantics of the OPTIONAL nodes
description, headers, body, and annotations for responses and method
declarations are the same" — and one class per side keeps that symmetry visible
instead of prefixing half the fields.

Every map preserves declaration order, which is a project invariant rather than
a convenience: a consumer generating documentation or a client SDK reproduces
the document's own order.

`source_decode.py` builds these from the stage-1 IR. The one decoder here is
`protocols:`, which the API root and a method share (docs/08 § 6.1).

See docs/08-templates-and-endpoints.md § 6 and docs/13-public-api.md § 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fastraml.parser.facets import make_string_facet
from fastraml.parser.includes import inline_include
from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import NodeKind, node_error

if TYPE_CHECKING:
    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape, Parameter, ScalarFacet
    from fastraml.yamlnode import Node

__all__ = [
    'VALID_PROTOCOLS',
    'Body',
    'EndPoint',
    'Operation',
    'Request',
    'Response',
    'base_uri_protocol',
    'decode_protocols',
]

#: Spec § Protocols: "a non-empty array of strings, of values HTTP and/or
#: HTTPS, and be case-insensitive". Stored upper-cased.
VALID_PROTOCOLS: Final = frozenset({'HTTP', 'HTTPS'})

#: A literal scheme at the start of a base URI. A templated one, `{scheme}://`,
#: does not match: its protocol is known only once a caller supplies it.
_SCHEME: Final = re.compile(r'([A-Za-z][A-Za-z0-9+.-]*):')


def decode_protocols(raml: Raml, node: Node, location: str) -> list[ScalarFacet[str]]:
    """`protocols:` at the API root or on a method: one rule for both (docs/08 § 6.1).

    A non-empty sequence of `HTTP` and `HTTPS` in any case, each stored
    upper-cased. An item may be an annotated scalar.
    """
    node, location = inline_include(raml, node, location)
    if node.kind is not NodeKind.SEQUENCE:
        raise node_error('protocols must be an array', location, node)
    if not node.content:
        raise node_error('protocols must not be empty', location, node)
    protocols = []
    for item in node.content:
        facet = make_string_facet(raml, None, item, location)
        upper = facet.value.upper()
        if upper not in VALID_PROTOCOLS:
            raise node_error('unknown protocol', location, item, info={'protocol': facet.value})
        facet.value = upper
        protocols.append(facet)
    return protocols


def base_uri_protocol(base_uri: str) -> str | None:
    """The protocol a base URI's literal scheme names, upper-cased, if HTTP or HTTPS.

    `None` for a templated or other scheme, or a reference without one: the
    spec's fallback to the protocol "included in the baseUri" then has nothing
    to read (docs/08 § 6.1).
    """
    match = _SCHEME.match(base_uri)
    if match is None:
        return None
    scheme = match.group(1).upper()
    return scheme if scheme in VALID_PROTOCOLS else None


@dataclass(slots=True, eq=False)
class Body:
    """One media type's payload declaration.

    A `body:` written without media-type keys is instantiated once per default
    media type, so several `Body` objects may share one declaration's position
    (docs/08 § 6.3).
    """

    id: int
    media_type: str
    location: str
    #: The payload's type. Always present: a body with no `type:` is `any`.
    shape: BaseShape | None = None
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: Whether `media_type` was written, or is a default media type a `body:`
    #: without one was instantiated for, at that `body:` key.
    media_type_written: bool = True

    def __repr__(self) -> str:
        return f'Body({self.media_type!r})'


@dataclass(slots=True, eq=False)
class Request:
    """What a method sends: headers, query, and a body per media type."""

    id: int
    location: str
    #: Declaration order. Each is a property declaration, so each is a shape.
    headers: dict[str, Parameter] = field(default_factory=dict)
    query_parameters: dict[str, Parameter] = field(default_factory=dict)
    #: Mutually exclusive with `query_parameters`; a whole shape, not a map.
    query_string: BaseShape | None = None
    bodies: dict[str, Body] = field(default_factory=dict)
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN


@dataclass(slots=True, eq=False)
class Response:
    """One status code's declaration."""

    id: int
    #: Normalised to text. Spec § Responses requires processors to treat numeric
    #: response keys as string keys in all situations.
    code: str
    location: str
    display_name: ScalarFacet[str] | None = None
    description: ScalarFacet[str] | None = None
    headers: dict[str, Parameter] = field(default_factory=dict)
    bodies: dict[str, Body] = field(default_factory=dict)
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'Response({self.code!r})'


@dataclass(slots=True, eq=False)
class Operation:
    """One HTTP method on one resource."""

    id: int
    method: str
    location: str
    display_name: ScalarFacet[str] | None = None
    description: ScalarFacet[str] | None = None
    #: As written on the method or a trait it applies, upper-cased, each with
    #: its annotations as at the API root; empty when neither states any. The
    #: method's effective protocols are these values, else
    #: `Raml.global_protocols` (docs/08 § 6.1).
    protocols: list[ScalarFacet[str]] = field(default_factory=list)
    request: Request | None = None
    #: Keyed by the status code normalised to text, in declaration order.
    responses: dict[str, Response] = field(default_factory=dict)
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    #: The trait references as written: the resource type's, the method's, then
    #: those the applied traits' own `is:` wrote. They have already been
    #: applied; they are retained because a consumer wants to know a method
    #: carried a trait.
    traits: list[DirectiveRef] = field(default_factory=list)
    #: The schemes in force, once P5 has resolved inheritance: this method's own
    #: if it declared any, otherwise a trait's, otherwise the resource's,
    #: otherwise the API's.
    secured_by: list[SecurityScheme] = field(default_factory=list)
    #: Whether `securedBy:` was written on this method, or on a trait applied
    #: to it, so an explicit `[]` or `[null]` is distinguishable from omission
    #: (docs/09 § A4).
    explicit_secured_by: bool = False
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'Operation({self.method!r})'


@dataclass(slots=True, eq=False)
class EndPoint:
    """One resource: its URI, its methods, and the resources beneath it."""

    id: int
    #: The key as written, `/users` or `/{userId}`.
    uri: str
    #: Ancestors' relative URIs concatenated, base URI *not* prepended
    #: (docs/08 § 6.1). This is the key `Raml.endpoints` uses.
    full_uri: str
    location: str
    display_name: ScalarFacet[str] | None = None
    description: ScalarFacet[str] | None = None
    #: Ancestor-declared parameters first, then this endpoint's, in path order
    #: (P6, docs/08 § 6.2). A template variable with no declaration gets a
    #: synthesised required `string`.
    uri_parameters: dict[str, Parameter] = field(default_factory=dict)
    operations: dict[str, Operation] = field(default_factory=dict)
    #: Nested resources, keyed by their *relative* URI as written.
    endpoints: dict[str, EndPoint] = field(default_factory=dict)
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    resource_type: DirectiveRef | None = None
    traits: list[DirectiveRef] = field(default_factory=list)
    #: Applied to this resource's own methods only: spec section Applying
    #: Security Schemes, "MUST NOT incorporate nested resources".
    secured_by: list[SecurityScheme] = field(default_factory=list)
    explicit_secured_by: bool = False
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'EndPoint({self.full_uri!r})'
