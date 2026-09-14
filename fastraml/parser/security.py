"""Security schemes: the declaration, the reference, and applying one.

docs/09-security-and-annotations.md Part A. Decoding a scheme is the easy half;
the half the fixtures exercise is rejection — every RAML document that fails
here fails because it declared a scheme the spec has no shape for.

Three things carry the design:

* **`describedBy:` reuses the operation decoders**, unchanged. Its headers,
  query parameters and responses are the same constructs, so the shapes it
  produces join `fragment_typedefs` and are resolved, unwrapped and validated by
  the ordinary passes without any of them knowing security schemes exist.
* **A `null` entry is a scheme**, not a `None` (§ A3). It carries a pre-built
  definition of type `null`, so nothing downstream branches on absence.
* **Per-application parameters live on the reference**, never on the definition
  (§ A5). One scheme applied to two operations with different scopes must not
  have the two interfere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from fastraml.domains import DomainLocation
from fastraml.errors import Accumulator, RamlError
from fastraml.parser.annotations import is_annotation_key, unmarshal_domain_extension
from fastraml.parser.facets import make_string_facet, scalar_str
from fastraml.parser.includes import note_include_ref
from fastraml.parser.source_decode import decode_responses
from fastraml.types.shape import make_parameter_map, make_shape
from fastraml.yamlnode import TAG_INCLUDE, Node, NodeKind, is_null, node_error, pairs

if TYPE_CHECKING:
    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.directives import SecurityScheme
    from fastraml.parser.endpoints import EndPoint, Response
    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape, Parameter, ScalarFacet

__all__ = [
    'SCHEME_TYPES',
    'SecuritySchemeDefinition',
    'SecuritySchemeDescription',
    'SecuritySchemeSettings',
    'apply_security_schemes',
    'make_security_scheme_definition',
]

FACET_TYPE: Final = 'type'
FACET_DISPLAY_NAME: Final = 'displayName'
FACET_DESCRIPTION: Final = 'description'
FACET_DESCRIBED_BY: Final = 'describedBy'
FACET_SETTINGS: Final = 'settings'

TYPE_NULL: Final = 'null'
TYPE_OAUTH1: Final = 'OAuth 1.0'
TYPE_OAUTH2: Final = 'OAuth 2.0'
TYPE_BASIC: Final = 'Basic Authentication'
TYPE_DIGEST: Final = 'Digest Authentication'
TYPE_PASS_THROUGH: Final = 'Pass Through'  # noqa: S105 - a scheme type name, not a credential

#: The settings key each declared type accepts. A type absent from this map has
#: no settings at all, so any `settings:` mapping under it is an error — which
#: is what catches `type: Basic Authentication` with an `accessTokenUri`
#: (docs/09 § A2).
SCHEME_TYPES: Final[dict[str, frozenset[str]]] = {
    TYPE_OAUTH1: frozenset({'requestTokenUri', 'authorizationUri', 'tokenCredentialsUri', 'signatures'}),
    TYPE_OAUTH2: frozenset({'authorizationUri', 'accessTokenUri', 'authorizationGrants', 'scopes'}),
    TYPE_BASIC: frozenset(),
    TYPE_DIGEST: frozenset(),
    TYPE_PASS_THROUGH: frozenset(),
}

#: Spec section Security Scheme Types, OAuth 1.0.
OAUTH1_SIGNATURES: Final = frozenset({'HMAC-SHA1', 'RSA-SHA1', 'PLAINTEXT'})

#: The four RFC 6749 grant names. Anything else must be an absolute URI: the
#: spec allows extension grants such as
#: `urn:ietf:params:oauth:grant-type:saml2-bearer`.
OAUTH2_GRANTS: Final = frozenset({'authorization_code', 'implicit', 'password', 'client_credentials'})

#: The two OAuth 2.0 grants that make `authorizationUri` mandatory.
OAUTH2_GRANTS_NEEDING_AUTHORIZATION_URI: Final = frozenset({'authorization_code', 'implicit'})


# -- the settings variants (docs/09 § A2) -------------------------------------


@dataclass(slots=True, eq=False)
class SecuritySchemeSettings:
    """What `settings:` held, per declared type.

    One class rather than six: the six differ only in which keys they accept and
    what they then require, and both of those are data (`SCHEME_TYPES` and
    `_validate`). Six near-empty subclasses would put the type name in two
    places and let them disagree.
    """

    scheme_type: str
    location: str
    #: The scalar-valued settings, by key as written. Empty for a type with none.
    values: dict[str, ScalarFacet[str]] = field(default_factory=dict)
    #: The sequence-valued ones: `signatures`, `authorizationGrants`, `scopes`.
    lists: dict[str, list[str]] = field(default_factory=dict)
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    value_pos: Position | None = None

    def __repr__(self) -> str:
        return f'SecuritySchemeSettings({self.scheme_type!r})'

    @property
    def scopes(self) -> list[str]:
        """The declared OAuth 2.0 scopes, or an empty list for any other type."""
        return self.lists.get('scopes', [])


@dataclass(slots=True, eq=False)
class SecuritySchemeDescription:
    """`describedBy:` — the same node vocabulary as an operation."""

    id: int
    location: str
    headers: dict[str, Parameter] = field(default_factory=dict)
    query_parameters: dict[str, Parameter] = field(default_factory=dict)
    query_string: BaseShape | None = None
    responses: dict[str, Response] = field(default_factory=dict)
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    value_pos: Position | None = None


@dataclass(slots=True, eq=False)
class SecuritySchemeDefinition:
    """One entry of a `securitySchemes:` map, or a SecurityScheme fragment."""

    id: int
    name: str
    location: str
    type: str = ''
    display_name: ScalarFacet[str] | None = None
    description: ScalarFacet[str] | None = None
    described_by: SecuritySchemeDescription | None = None
    settings: SecuritySchemeSettings | None = None
    link: SecuritySchemeDefinition | None = None
    link_uri: str | None = None
    annotations: dict[str, DomainExtension] = field(default_factory=dict)
    key_pos: Position | None = None
    value_pos: Position | None = None

    def __repr__(self) -> str:
        return f'SecuritySchemeDefinition({self.name!r}, {self.type!r})'

    def resolved(self) -> SecuritySchemeDefinition:
        """Itself, or the definition an `!include` pointed at."""
        return self if self.link is None else self.link


# -- decoding a declaration ---------------------------------------------------


def make_security_scheme_definition(  # noqa: PLR0912 - one pass over the declaration's key vocabulary
    raml: Raml, key_node: Node | None, value_node: Node, location: str
) -> SecuritySchemeDefinition:
    """Decode one security-scheme declaration."""
    definition = SecuritySchemeDefinition(
        id=raml.next_id(),
        name=key_node.value if key_node is not None else '',
        location=location,
        key_pos=(key_node if key_node is not None else value_node).position,
        value_pos=value_node.full_position,
    )
    if value_node.tag == TAG_INCLUDE:
        definition.link_uri = note_include_ref(raml, value_node, location)
        return definition
    if is_null(value_node):
        raise node_error('security scheme must declare a type', location, value_node)
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('security scheme definition must be a mapping', location, value_node)

    settings_node: Node | None = None
    type_node: Node | None = None
    accumulator = Accumulator()
    with raml.target_scope(DomainLocation.SECURITY_SCHEME):
        for key, value in pairs(value_node):
            name = key.value
            try:
                if name == FACET_TYPE:
                    definition.type = scalar_str(value, location)
                    type_node = value
                elif name == FACET_DISPLAY_NAME:
                    definition.display_name = make_string_facet(raml, key, value, location)
                elif name == FACET_DESCRIPTION:
                    definition.description = make_string_facet(raml, key, value, location)
                elif name == FACET_DESCRIBED_BY:
                    definition.described_by = _decode_described_by(raml, value, location)
                elif name == FACET_SETTINGS:
                    settings_node = value
                elif is_annotation_key(name):
                    extension = unmarshal_domain_extension(raml, location, key, value)
                    definition.annotations[extension.name] = extension
                else:
                    raise node_error('unknown field', location, key, info={'field': name})
            except RamlError as err:
                accumulator.add(err)

    if type_node is None:
        accumulator.add(node_error('security scheme must declare a type', location, value_node))
    else:
        try:
            definition.settings = _make_settings(raml, definition.type, type_node, settings_node, location)
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()
    return definition


def _decode_described_by(raml: Raml, node: Node, location: str) -> SecuritySchemeDescription:
    """`describedBy:` — headers, query, responses, decoded exactly as a method's."""
    description = SecuritySchemeDescription(id=raml.next_id(), location=location, value_pos=node.full_position)
    if is_null(node):
        return description
    if node.kind is not NodeKind.MAPPING:
        raise node_error('describedBy must be a mapping', location, node)

    accumulator = Accumulator()
    for key, value in pairs(node):
        name = key.value
        try:
            if name == 'headers':
                description.headers = make_parameter_map(raml, value, location, 'header')
            elif name == 'queryParameters':
                description.query_parameters = make_parameter_map(raml, value, location, 'query')
            elif name == 'queryString':
                description.query_string = make_shape(raml, key, value, location)
                raml.put_typedef(description.query_string.location, description.query_string)
            elif name == 'responses':
                description.responses = decode_responses(raml, value, location)
            elif is_annotation_key(name):
                extension = unmarshal_domain_extension(raml, location, key, value)
                description.annotations[extension.name] = extension
            else:
                raise node_error('unknown field in describedBy', location, key, info={'field': name})
        except RamlError as err:
            accumulator.add(err)

    if description.query_string is not None and description.query_parameters:
        accumulator.add(node_error('queryString and queryParameters are mutually exclusive', location, node))
    accumulator.raise_if_any()
    return description


def _make_settings(
    raml: Raml, scheme_type: str, type_node: Node, settings_node: Node | None, location: str
) -> SecuritySchemeSettings:
    """Decode `settings:` for the declared type, then check what it requires."""
    allowed = SCHEME_TYPES.get(scheme_type)
    if allowed is None:
        if not scheme_type.startswith('x-'):
            # Spec section Security Scheme Types: the five named types, or an
            # `x-` extension. Anything else is a typo, not an extension.
            raise node_error('unknown security scheme type', location, type_node, info={'type': scheme_type})
        allowed = frozenset()

    settings = SecuritySchemeSettings(scheme_type=scheme_type, location=location)
    if settings_node is not None and not is_null(settings_node):
        if not allowed:
            raise node_error(
                'security scheme type has no settings', location, settings_node, info={'type': scheme_type}
            )
        settings.value_pos = settings_node.full_position
        _decode_settings(raml, settings, allowed, settings_node, location)
    _validate_settings(settings, settings_node if settings_node is not None else type_node, location)
    return settings


#: Which of the accepted keys hold a sequence rather than a scalar.
_LIST_SETTINGS: Final = frozenset({'signatures', 'authorizationGrants', 'scopes'})


def _decode_settings(
    raml: Raml, settings: SecuritySchemeSettings, allowed: frozenset[str], node: Node, location: str
) -> None:
    if node.kind is not NodeKind.MAPPING:
        raise node_error('security scheme settings must be a mapping', location, node)
    accumulator = Accumulator()
    with raml.target_scope(DomainLocation.SECURITY_SCHEME_SETTINGS):
        for key, value in pairs(node):
            name = key.value
            try:
                if is_annotation_key(name):
                    extension = unmarshal_domain_extension(raml, location, key, value)
                    settings.annotations[extension.name] = extension
                elif name not in allowed:
                    # A key the declared type does not define. Caught rather
                    # than ignored: `Basic Authentication` with an
                    # `accessTokenUri` is a misunderstanding, not a no-op.
                    raise node_error(
                        'unknown security scheme setting',
                        location,
                        key,
                        info={'setting': name, 'type': settings.scheme_type},
                    )
                elif name in _LIST_SETTINGS:
                    settings.lists[name] = _string_sequence(value, location, name)
                else:
                    settings.values[name] = make_string_facet(raml, key, value, location)
            except RamlError as err:
                accumulator.add(err)
    accumulator.raise_if_any()


def _string_sequence(node: Node, location: str, facet: str) -> list[str]:
    if node.kind is not NodeKind.SEQUENCE:
        raise node_error('setting must be a sequence', location, node, info={'setting': facet})
    return [scalar_str(item, location) for item in node.content]


def _validate_settings(settings: SecuritySchemeSettings, node: Node, location: str) -> None:
    """What each type requires, once its settings are decoded (docs/09 § A2)."""
    accumulator = Accumulator()
    if settings.scheme_type == TYPE_OAUTH1:
        for required in ('requestTokenUri', 'authorizationUri', 'tokenCredentialsUri'):
            if not settings.values.get(required, None) or not settings.values[required].value:
                accumulator.add(
                    node_error('security scheme setting is required', location, node, info={'setting': required})
                )
        for signature in settings.lists.get('signatures', []):
            if signature not in OAUTH1_SIGNATURES:
                accumulator.add(node_error('unknown signature', location, node, info={'signature': signature}))
    elif settings.scheme_type == TYPE_OAUTH2:
        accumulator.add(_validate_oauth2(settings, node, location))
    accumulator.raise_if_any()


def _validate_oauth2(settings: SecuritySchemeSettings, node: Node, location: str) -> RamlError | None:
    if not settings.values.get('accessTokenUri', None) or not settings.values['accessTokenUri'].value:
        return node_error('security scheme setting is required', location, node, info={'setting': 'accessTokenUri'})
    grants = settings.lists.get('authorizationGrants', [])
    for grant in grants:
        if grant not in OAUTH2_GRANTS and not _is_absolute_uri(grant):
            # Not one of the four RFC 6749 names, and not an extension grant.
            return node_error('unknown authorization grant', location, node, info={'grant': grant})
    if any(grant in OAUTH2_GRANTS_NEEDING_AUTHORIZATION_URI for grant in grants) and not settings.values.get(
        'authorizationUri', None
    ):
        return node_error('security scheme setting is required', location, node, info={'setting': 'authorizationUri'})
    return None


def _is_absolute_uri(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme) and bool(parsed.netloc or parsed.path)


# -- the null scheme (docs/09 section A3) -------------------------------------


def null_definition(raml: Raml, location: str) -> SecuritySchemeDefinition:
    """The definition a `securedBy: [null]` entry binds to (docs/09 § A3)."""
    return SecuritySchemeDefinition(
        id=raml.next_id(),
        name=TYPE_NULL,
        location=location,
        type=TYPE_NULL,
        settings=SecuritySchemeSettings(scheme_type=TYPE_NULL, location=location),
    )


# -- P5: inheritance and application (docs/09 §§ A4, A5) ----------------------


def apply_security_schemes(raml: Raml) -> None:
    """Propagate `securedBy:` down one level, then bind every reference.

    Three levels, each *replacing* the one above: API root, resource, method.
    Replacing rather than appending is what makes `securedBy: [null]` on a method
    remove inherited security instead of adding to it.
    """
    resolver = raml.resolver_at(raml.location)
    accumulator = Accumulator()
    for endpoint in raml.endpoints.values():
        _inherit(endpoint)
        for operation in endpoint.operations.values():
            for scheme in operation.secured_by:
                try:
                    _bind(raml, scheme, resolver)
                except RamlError as err:
                    accumulator.add(err)
        for scheme in endpoint.secured_by:
            try:
                _bind(raml, scheme, resolver)
            except RamlError as err:
                accumulator.add(err)
    for scheme in raml.global_secured_by:
        try:
            _bind(raml, scheme, resolver)
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()


def _inherit(endpoint: EndPoint) -> None:
    """docs/09 § A4. Resource-level schemes do not reach nested resources."""
    if not endpoint.explicit_secured_by:
        return
    for operation in endpoint.operations.values():
        if not operation.explicit_secured_by:
            operation.secured_by = endpoint.secured_by


def _bind(raml: Raml, scheme: SecurityScheme, resolver: Any) -> None:
    """Resolve the name, then compile whatever the application supplied.

    A scheme name resolves against the **API**, not lexically — the one place
    this differs from a trait or a resource type. Only a `Library` and an
    `APIFragment` declare `securitySchemes:`, so a `securedBy:` written inside a
    trait fragment has no lexical namespace that could hold one.
    """
    if scheme.definition is not None:
        return
    if scheme.is_null:
        scheme.definition = null_definition(raml, scheme.location)
        return
    if resolver is None:
        raise RamlError.new('no scope to resolve a security scheme name in', scheme.location, scheme.value_pos)
    try:
        definition = resolver.security_scheme_definition(scheme.name)
    except LookupError as err:
        raise RamlError.wrap(
            'get security scheme definition', err, scheme.location, scheme.value_pos, info={'scheme': scheme.name}
        ) from err
    scheme.definition = definition
    if scheme.params:
        _apply_params(scheme, definition.resolved())


def _apply_params(scheme: SecurityScheme, definition: SecuritySchemeDefinition) -> None:
    """docs/09 § A5. Only OAuth 2.0 takes any, and it narrows `scopes`."""
    settings = definition.settings
    if settings is None or settings.scheme_type != TYPE_OAUTH2:
        if 'scopes' in scheme.params:
            raise RamlError.new(
                'scopes override is only valid for OAuth 2.0 schemes',
                scheme.location,
                scheme.value_pos,
                info={'scheme': scheme.name},
            )
        return
    node = scheme.params.get('scopes')
    if node is None:
        return
    requested = _string_sequence(node, scheme.location, 'scopes') if node.kind is NodeKind.SEQUENCE else [node.value]
    declared = set(settings.scopes)
    for scope in requested:
        if scope not in declared:
            raise RamlError.new(
                'scope is not declared by the security scheme',
                scheme.location,
                scheme.value_pos,
                info={'scope': scope, 'scheme': scheme.name},
            )
    # On the reference, never on the definition: one scheme applied to two
    # operations with different scopes must not have the two interfere.
    scheme.compiled_params = requested
