"""Stage 1 — the endpoint IR, which is a YAML tree with the directives taken off.

Decoding a resource produces an *intermediate representation*, not a model. The
reason is docs/08 section 2: applying a trait means putting the trait's branch of
the document underneath the method's branch, and that algorithm is defined on
the YAML tree. A parser that built `Operation`, `Response` and `BaseShape`
objects first would have to reimplement the merge over the model — separately
for `headers`, `queryParameters`, `responses`, `body` and every shape facet —
and then re-resolve type names, because a name contributed by a trait was never
resolved in the operation's scope.

So stage 1 consumes exactly four kinds of key and leaves everything else as it
found it. Stage 2 (`source_decode.py`) turns what survives into the model, once,
after Phase 6's merge has finished rearranging it.

See docs/08-templates-and-endpoints.md section 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from pyraml.errors import Accumulator, RamlError
from pyraml.parser.directives import DirectiveRef, decode_secured_by, decode_trait_refs, decode_type_ref
from pyraml.positions import UNKNOWN, Position
from pyraml.yamlnode import Node, NodeKind, is_null, node_error, pairs

if TYPE_CHECKING:
    from pyraml.registry import ParseCtx, Raml

__all__ = [
    'METHODS',
    'SourceEndPoint',
    'SourceOperation',
    'make_source_endpoint',
]

#: The HTTP methods a resource may declare (spec section Methods). `?`-suffixed
#: spellings are legal only inside a resource type, where they mean "apply this
#: only if the target already declares it"; the suffix is chomped by whoever
#: compiles the template, so plain names are what reach here.
METHODS: Final = frozenset({'get', 'patch', 'put', 'post', 'delete', 'options', 'head', 'trace', 'connect'})

FACET_TYPE: Final = 'type'
FACET_IS: Final = 'is'
FACET_SECURED_BY: Final = 'securedBy'


@dataclass(slots=True, eq=False)
class SourceOperation:
    """One method, with its directives decoded and its body still YAML."""

    id: int
    method: str
    location: str
    #: `is:` written here. `rt_traits` is filled by Phase 6 with traits arriving
    #: through a resource type; the split is what keeps the four priority
    #: classes of docs/08 section 5.2 distinguishable after the merge.
    traits: list[DirectiveRef] = field(default_factory=list)
    rt_traits: list[DirectiveRef] = field(default_factory=list)
    secured_by: list[DirectiveRef] = field(default_factory=list)
    #: Whether `securedBy:` was written here at all. `[]` from an explicit empty
    #: sequence and `[]` from silence mean different things (docs/09 part A).
    explicit_secured_by: bool = False
    #: Everything stage 1 did not consume, as a mapping node. `None` when the
    #: method was declared with no body at all (`get:`).
    body: Node | None = None
    #: The scope names in this branch resolve in, captured at decode time.
    scope: ParseCtx | None = None
    #: Filled by Phase 6: which scope a grafted node came from (docs/08 § 6).
    provenance: dict[Node, ParseCtx] = field(default_factory=dict)
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'SourceOperation({self.method!r})'


@dataclass(slots=True, eq=False)
class SourceEndPoint:
    """One resource, its methods and its nested resources."""

    id: int
    #: The key as written, `/users` or `/{userId}`.
    uri: str
    #: Ancestors' relative URIs concatenated. The base URI is *not* prepended —
    #: it is exposed separately on the API (docs/08 section 8.1).
    full_uri: str
    location: str
    resource_type: DirectiveRef | None = None
    traits: list[DirectiveRef] = field(default_factory=list)
    rt_traits: list[DirectiveRef] = field(default_factory=list)
    secured_by: list[DirectiveRef] = field(default_factory=list)
    explicit_secured_by: bool = False
    #: Declaration order, as everywhere the model is exposed.
    operations: dict[str, SourceOperation] = field(default_factory=dict)
    endpoints: dict[str, SourceEndPoint] = field(default_factory=dict)
    body: Node | None = None
    scope: ParseCtx | None = None
    provenance: dict[Node, ParseCtx] = field(default_factory=dict)
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'SourceEndPoint({self.full_uri!r})'


def _retained(kept: list[Node], source: Node) -> Node | None:
    """The leftover keys as a fresh mapping, carrying the original's position.

    A new node rather than a filtered view of the old one: Phase 6 merges into
    this and must not reach the document's own tree, which invariant "structural
    merge never mutates either input" depends on.
    """
    if not kept:
        return None
    return Node(
        NodeKind.MAPPING,
        source.tag,
        source.value,
        kept,
        source.line,
        source.column,
        source.end_line,
        source.end_column,
    )


def make_source_operation(raml: Raml, method: str, key: Node, value: Node, location: str) -> SourceOperation:
    """Decode one method into IR. Directives out, everything else retained."""
    operation = SourceOperation(
        id=raml.next_id(),
        method=method,
        location=location,
        scope=raml.current_ctx(),
        key_pos=key.position,
        value_pos=value.full_position,
    )
    if is_null(value):
        # `get:` with nothing under it is a legal, empty method.
        return operation
    if value.kind is not NodeKind.MAPPING:
        raise node_error('method must be a mapping', location, value, info={'method': method})

    kept: list[Node] = []
    for child_key, child_value in pairs(value):
        # A method takes two of the three directives: `type:` is a resource's.
        if child_key.value == FACET_IS:
            operation.traits = decode_trait_refs(child_value, location)
        elif child_key.value == FACET_SECURED_BY:
            operation.secured_by = decode_secured_by(child_value, location)
            operation.explicit_secured_by = True
        else:
            kept += (child_key, child_value)
    operation.body = _retained(kept, value)
    return operation


def make_source_endpoint(raml: Raml, key: Node, value: Node, location: str, *, parent_uri: str = '') -> SourceEndPoint:
    """Decode one `/path` key into IR, recursing into methods and subresources.

    Errors accumulate across siblings: one malformed method should not hide the
    rest of the resource.
    """
    uri = key.value
    endpoint = SourceEndPoint(
        id=raml.next_id(),
        uri=uri,
        full_uri=parent_uri + uri,
        location=location,
        scope=raml.current_ctx(),
        key_pos=key.position,
        value_pos=value.full_position,
    )
    if is_null(value):
        return endpoint
    if value.kind is not NodeKind.MAPPING:
        raise node_error('resource must be a mapping', location, value, info={'resource': uri})

    kept: list[Node] = []
    accumulator = Accumulator()
    for child_key, child_value in pairs(value):
        name = child_key.value
        try:
            if name == FACET_TYPE:
                endpoint.resource_type = decode_type_ref(child_value, location)
            elif name == FACET_IS:
                endpoint.traits = decode_trait_refs(child_value, location)
            elif name == FACET_SECURED_BY:
                endpoint.secured_by = decode_secured_by(child_value, location)
                endpoint.explicit_secured_by = True
            elif name in METHODS:
                if name in endpoint.operations:
                    raise node_error('duplicate method', location, child_key, info={'method': name})
                endpoint.operations[name] = make_source_operation(raml, name, child_key, child_value, location)
            elif name.startswith('/'):
                if name in endpoint.endpoints:
                    raise node_error('duplicate resource', location, child_key, info={'resource': name})
                endpoint.endpoints[name] = make_source_endpoint(
                    raml, child_key, child_value, location, parent_uri=endpoint.full_uri
                )
            else:
                kept += (child_key, child_value)
        except RamlError as err:
            accumulator.add(err)
    endpoint.body = _retained(kept, value)
    accumulator.raise_if_any()
    return endpoint
