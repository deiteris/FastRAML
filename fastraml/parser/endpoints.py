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

Nothing here decodes. `source_decode.py` builds these from the stage-1 IR.

See docs/08-templates-and-endpoints.md section 8 and docs/13-public-api.md § 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fastraml.positions import UNKNOWN, Position

if TYPE_CHECKING:
    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.types.base import BaseShape, Parameter, ScalarFacet

__all__ = [
    'VALID_PROTOCOLS',
    'Body',
    'EndPoint',
    'Operation',
    'Request',
    'Response',
]

#: Spec § Protocols: "the value is an array of strings, of values `HTTP` and/or
#: `HTTPS`". Compared case-insensitively, and stored upper-cased. Here rather
#: than in either decoder because both the API root and a method declare the
#: facet, and the rule has to be the same in both.
VALID_PROTOCOLS: Final = frozenset({'http', 'https'})


@dataclass(slots=True, eq=False)
class Body:
    """One media type's payload declaration.

    A `body:` written without media-type keys is instantiated once per default
    media type, so several `Body` objects may share one declaration's position
    (docs/08 section 8.3).
    """

    id: int
    media_type: str
    location: str
    #: The payload's type. Always present: a body with no `type:` is `any`.
    shape: BaseShape | None = None
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

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
    #: As written, so `200` and `2xx` both round-trip.
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
    protocols: list[str] = field(default_factory=list)
    request: Request | None = None
    #: Keyed by the status code as written, in declaration order.
    responses: dict[str, Response] = field(default_factory=dict)
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    #: The trait references as written. They have already been applied; they are
    #: retained because a consumer wants to know a method carried a trait.
    traits: list[DirectiveRef] = field(default_factory=list)
    #: The schemes in force, once P5 has resolved inheritance: this method's own
    #: if it declared any, otherwise the resource's, otherwise the API's.
    secured_by: list[SecurityScheme] = field(default_factory=list)
    #: Whether `securedBy:` was written on this method at all. `[]` from an
    #: explicit empty sequence and `[]` from silence mean different things, and
    #: it is what makes `securedBy: [null]` *remove* inherited security
    #: (docs/09 section A4).
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
    #: (docs/08 section 8.1). This is the key `Raml.endpoints` uses.
    full_uri: str
    location: str
    display_name: ScalarFacet[str] | None = None
    description: ScalarFacet[str] | None = None
    #: Ancestor-declared parameters first, then this endpoint's, in path order
    #: (P6, docs/08 section 8.2). A template variable with no declaration gets a
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
