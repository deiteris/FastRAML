"""`fastapi.security` schemes as RAML security schemes, and each route's `securedBy`."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, Final

from fastapi.security.base import SecurityBase
from raml_document import SecuredBy, SecurityScheme, Yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

    from raml_document.from_pydantic import Walk

__all__ = ['requirements', 'schemes', 'secured_by']

#: An OAuth2 flow as FastAPI names it -> as RAML names it.
GRANTS: Final[dict[str, str]] = {
    'authorizationCode': 'authorization_code',
    'clientCredentials': 'client_credentials',
    'password': 'password',
    'implicit': 'implicit',
}
#: FastAPI's `in` of an API key -> the `describedBy` node that carries it. A
#: cookie travels in the `Cookie` header, which is where RAML can see it.
KEY_PLACES: Final[dict[str, str]] = {'header': 'headers', 'query': 'queryParameters', 'cookie': 'headers'}


def requirements(dependant: Any) -> Iterator[tuple[SecurityBase, list[str]]]:
    """Every security scheme a dependency tree runs, with the OAuth scopes asked for there.

    Two layouts. Older releases record a `SecurityRequirement` on the
    dependant that calls the scheme; newer ones make the scheme that
    dependant's `call` and split its scopes into the ones inherited and its
    own.
    """
    legacy = getattr(dependant, 'security_requirements', None)
    if legacy is not None:
        for requirement in legacy:
            yield requirement.security_scheme, list(requirement.scopes or ())
    else:
        call = dependant.call
        while isinstance(call, functools.partial):
            call = call.func
        if isinstance(call, SecurityBase):
            scopes = [*(dependant.parent_oauth_scopes or ()), *(dependant.own_oauth_scopes or ())]
            yield call, list(dict.fromkeys(scopes))
    for sub in dependant.dependencies:
        yield from requirements(sub)


def schemes(routes: list[Any], walk: Walk) -> dict[str, SecurityScheme]:
    """`securitySchemes:`, harvested off each route's dependency tree."""
    schemes: dict[str, SecurityScheme] = {}
    for route in routes:
        for scheme, _ in requirements(route.dependant):
            if scheme.scheme_name in schemes:
                continue
            built = _scheme(scheme, walk)
            if built is not None:
                schemes[scheme.scheme_name] = built
    return schemes


def _value(item: Any) -> Any:
    """An enum member's value; anything else as it is."""
    return getattr(item, 'value', item)


def _scheme(scheme: SecurityBase, walk: Walk) -> SecurityScheme | None:  # noqa: PLR0911 - one return per kind
    """One FastAPI scheme as the RAML scheme that says the same thing.

    RAML names five kinds. Everything that is a credential in a named header,
    query parameter or cookie -- a bearer token, an API key -- is `Pass
    Through`, whose `describedBy` says where it travels. OpenID Connect has no
    RAML kind, so it is a custom `x-` one whose description names its discovery
    URL.
    """
    # Typed as the base model; `type_` decides which subclass it is.
    model: Any = scheme.model
    kind = _value(model.type_)
    described_by: dict[str, Yaml]
    match kind:
        case 'oauth2':
            return _oauth2(model.model_dump(exclude_none=True), model.description)
        case 'http' if model.scheme.lower() == 'basic':
            return SecurityScheme(type='Basic Authentication', description=model.description)
        case 'http' if model.scheme.lower() == 'digest':
            return SecurityScheme(type='Digest Authentication', description=model.description)
        case 'http':
            detail = f'{model.scheme} credentials'
            if getattr(model, 'bearerFormat', None):
                detail = f'{detail}, {model.bearerFormat}'
            described_by = {'headers': {'Authorization': {'type': 'string', 'description': detail}}}
            return SecurityScheme(type='Pass Through', description=model.description, described_by=described_by)
        case 'apiKey':
            place = _value(model.in_)
            if place == 'cookie':
                entry: Yaml = {'Cookie': {'type': 'string', 'description': f'carries the {model.name} cookie'}}
            else:
                entry = {model.name: 'string'}
            described_by = {KEY_PLACES[place]: entry}
            return SecurityScheme(type='Pass Through', description=model.description, described_by=described_by)
        case 'openIdConnect':
            # A custom scheme has no `settings:`, so the discovery URL is said
            # in the description.
            discovery = f'OpenID Connect, discovered at {model.openIdConnectUrl}'
            return SecurityScheme(
                type='x-openid-connect',
                description=f'{model.description}\n\n{discovery}' if model.description else discovery,
                described_by={'headers': {'Authorization': 'string'}},
            )
    walk.drop(scheme.scheme_name, f'security scheme kind {kind!r} has no RAML form')
    return None


def _oauth2(model: dict[str, Any], description: str | None) -> SecurityScheme:
    """Every flow the scheme declares, as RAML's one settings node.

    RAML states the grants as a list beside one pair of URIs; the URIs come from
    the first flow that has each.
    """
    flows: dict[str, dict[str, Any]] = model.get('flows') or {}
    settings: dict[str, Yaml] = {'authorizationGrants': [GRANTS.get(flow, flow) for flow in flows]}
    authorization = next(
        (config['authorizationUrl'] for config in flows.values() if 'authorizationUrl' in config), None
    )
    token = next((config['tokenUrl'] for config in flows.values() if 'tokenUrl' in config), None)
    if authorization:
        settings['authorizationUri'] = authorization
    if token:
        settings['accessTokenUri'] = token
    scopes = [scope for config in flows.values() for scope in config.get('scopes') or {}]
    if scopes:
        settings['scopes'] = sorted(set(scopes))
    return SecurityScheme(type='OAuth 2.0', description=description, settings=settings)


def secured_by(route: Any, schemes: dict[str, SecurityScheme], at: str, walk: Walk) -> list[SecuredBy]:
    """`securedBy:` for one route: each scheme it runs that the document declares.

    A scheme that could not be declared is left out, since a `securedBy`
    naming an undeclared scheme does not parse; its drop already says why. One
    scheme reached twice, with different scopes, is one entry with both.
    """
    merged: dict[str, list[str]] = {}
    enforcing: list[str] = []
    for scheme, scopes in requirements(route.dependant):
        name = scheme.scheme_name
        if name not in schemes:
            continue
        if name not in merged and getattr(scheme, 'auto_error', True):
            enforcing.append(name)
        known = merged.setdefault(name, [])
        known.extend(scope for scope in scopes if scope not in known)
    if len(enforcing) > 1:
        # FastAPI runs every dependency, and each of these refuses a request
        # without its credential; RAML reads a `securedBy` list as choices.
        walk.drop(at, f'{", ".join(enforcing)} are each required, and RAML reads securedBy as alternatives')
    out: list[SecuredBy] = []
    for name, scopes in merged.items():
        oauth = schemes[name].type == 'OAuth 2.0'
        if scopes and not oauth:
            walk.drop(at, f'{name!r} carries scopes {scopes}, and RAML scopes belong to OAuth 2.0')
        out.append(SecuredBy(scheme=name, scopes=scopes if oauth else []))
    return out
