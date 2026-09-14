"""The three directive references a resource or method may carry.

`type:` names a resource type, `is:` names traits, `securedBy:` names security
schemes. All three share one shape — a name, optional parameters, and the
position they were written at — because all three are *applications* of a
template-like declaration, and the differences are entirely in how the name
resolves and what the application does.

Stage 1 builds these and stores them; nothing applies them. Resolution is
Phases 6 (`traits.py`, `resourcetypes.py`) and 7 (`security.py`), which own the
*definitions* these point at. Keeping the references here rather than in those
modules is what lets stage 1 exist before either phase does.

See docs/08-templates-and-endpoints.md sections 3 and 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastraml.positions import UNKNOWN, Position
from fastraml.yamlnode import NodeKind, is_null, node_error, pairs

if TYPE_CHECKING:
    from fastraml.parser.resourcetypes import ResourceTypeDefinition
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.parser.traits import TraitDefinition
    from fastraml.registry import ParseCtx, Raml
    from fastraml.yamlnode import Node

__all__ = [
    'DirectiveRef',
    'SecurityScheme',
    'decode_secured_by',
    'decode_trait_refs',
    'decode_type_ref',
    'make_security_schemes',
]


@dataclass(slots=True, eq=False)
class DirectiveRef:
    """One `type:`, `is:` or `securedBy:` entry.

    `params` holds the argument nodes undigested: a parameter value may be any
    scalar, and substituting it is Phase 6's business, not decoding it.
    """

    name: str
    #: Written as `{name: {p: v}}` rather than a bare name. Empty for a bare one.
    params: dict[str, Node] = field(default_factory=dict)
    location: str = ''
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: `securedBy: [null]` — the explicit "no scheme" entry (docs/09 § A).
    is_null_scheme: bool = False
    #: The namespace this *name* resolves in — the document the directive is
    #: physically written in, not the one it is applied to. A resource type's
    #: own `is:` entries resolve against the resource type's file even after the
    #: merge has moved them onto an operation in another (docs/08 section 5.2).
    scope: ParseCtx | None = None
    #: The declaration this name resolved to, filled in by whichever of
    #: `traits.py` and `resourcetypes.py` resolved it. A consumer asking what
    #: was applied reads this rather than matching the name again: two libraries
    #: may declare one name, and a lookup cannot tell them apart
    #: (docs/16 § 2.2). `None` where the name matched nothing.
    resolved: TraitDefinition | ResourceTypeDefinition | None = None

    def __repr__(self) -> str:
        return f'DirectiveRef({self.name!r})'


def _one_ref(node: Node, location: str, scope: ParseCtx | None, *, what: str) -> DirectiveRef:
    """A reference in either spelling: a bare name, or `{name: {params}}`."""
    if node.kind is NodeKind.SCALAR:
        if is_null(node):
            # Only `securedBy:` gives this a meaning; the callers that do not
            # want it reject the result.
            return DirectiveRef(
                name='',
                location=location,
                key_pos=node.position,
                value_pos=node.full_position,
                is_null_scheme=True,
                scope=scope,
            )
        return DirectiveRef(
            name=node.value, location=location, key_pos=node.position, value_pos=node.full_position, scope=scope
        )

    if node.kind is not NodeKind.MAPPING:
        raise node_error(f'{what} must be a name or a name with parameters', location, node)

    content = node.content
    found = len(content) // 2
    if found != 1:
        # `{a: …, b: …}` is two applications written as one, which the spec has
        # no form for — a sequence is how you apply two.
        raise node_error(f'{what} must name exactly one', location, node, info={'found': found})

    key, value = content
    params: dict[str, Node] = {}
    if not is_null(value):
        if value.kind is not NodeKind.MAPPING:
            raise node_error(f'{what} parameters must be a mapping', location, value)
        params = {param.value: argument for param, argument in pairs(value)}
    return DirectiveRef(
        name=key.value,
        params=params,
        location=location,
        key_pos=key.position,
        value_pos=value.full_position,
        scope=scope,
    )


def _ref_list(node: Node, location: str, scope: ParseCtx | None, *, what: str) -> list[DirectiveRef]:
    """A sequence of references, or a single one written without the sequence."""
    if is_null(node):
        return []
    if node.kind is NodeKind.SEQUENCE:
        return [_one_ref(item, location, scope, what=what) for item in node.content]
    return [_one_ref(node, location, scope, what=what)]


def decode_type_ref(node: Node, location: str, scope: ParseCtx | None = None) -> DirectiveRef:
    """`type:` on a resource — exactly one resource type, never a sequence.

    A sequence in a `type:` position means multiple inheritance for a *type
    declaration*; a resource has no such form (docs/08 section 5.1).
    """
    if node.kind is NodeKind.SEQUENCE:
        raise node_error('resource type must be a single reference', location, node)
    ref = _one_ref(node, location, scope, what='resource type')
    if ref.is_null_scheme:
        raise node_error('resource type must not be null', location, node)
    return ref


def decode_trait_refs(node: Node, location: str, scope: ParseCtx | None = None) -> list[DirectiveRef]:
    """`is:` — a sequence of trait references, and a sequence it must be.

    Spec section Traits: "The value MUST be an array of any number of elements".
    A bare `is: chargeable` reads as though it should work and no valid TCK
    fixture writes one; go-raml accepts it, which is why
    `Traits/is-node-format/invalid-is-single-value.raml` fails there.
    """
    if not is_null(node) and node.kind is not NodeKind.SEQUENCE:
        raise node_error('is must be a sequence', location, node)
    refs = _ref_list(node, location, scope, what='trait')
    for ref in refs:
        if ref.is_null_scheme:
            raise node_error('trait must not be null', location, node)
    return refs


def decode_secured_by(node: Node, location: str, scope: ParseCtx | None = None) -> list[DirectiveRef]:
    """`securedBy:` — a sequence, where a null entry is meaningful.

    Spec section Applying Security Schemes: a `null` entry says the method may
    be called without authentication, which is not the same as declaring no
    `securedBy:` at all — the latter inherits from the resource or the API.
    """
    return _ref_list(node, location, scope, what='security scheme')


# -- the one reference that survives into the model ---------------------------

NULL_SCHEME_NAME = 'null'


@dataclass(slots=True, eq=False)
class SecurityScheme:
    """One `securedBy:` entry, promoted for the model to carry.

    The odd one out: applying a trait or a resource type produces a merged tree
    and leaves nothing on the reference, but applying a security scheme produces
    a *binding*, and a binding needs somewhere to live. So this sits beside
    `DirectiveRef` rather than in `security.py` — same reason the three
    references share this module, and it keeps `source_decode.py` (which builds
    these in stage 2) from having to import the module that resolves them.

    See docs/09-security-and-annotations.md sections A1, A3 and A5.
    """

    id: int
    name: str
    location: str
    #: The declaration this names. `None` until P5 binds it.
    definition: SecuritySchemeDefinition | None = None
    #: `securedBy: [oauth_2_0: {scopes: [ADMIN]}]` — undigested until P5.
    params: dict[str, Node] = field(default_factory=dict)
    #: What the settings made of `params`: OAuth 2.0's narrowed scopes, or None.
    compiled_params: list[str] | None = None
    #: `securedBy: [null]` — "may also be called with no scheme". It binds to a
    #: real definition of type `null`, so nothing downstream tests for absence.
    is_null: bool = False
    value_pos: Position = UNKNOWN

    def __repr__(self) -> str:
        return f'SecurityScheme({self.name!r})'


def make_security_schemes(raml: Raml, refs: list[DirectiveRef]) -> list[SecurityScheme]:
    """Promote the stage-1 `securedBy:` references into scheme references."""
    return [
        SecurityScheme(
            id=raml.next_id(),
            name=NULL_SCHEME_NAME if ref.is_null_scheme else ref.name,
            location=ref.location,
            params=ref.params,
            is_null=ref.is_null_scheme,
            value_pos=ref.value_pos,
        )
        for ref in refs
    ]
