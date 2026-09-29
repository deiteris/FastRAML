"""`fastapi.security` schemes as RAML security schemes, and each route's `securedBy`."""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Any, Final

from fastapi.security.base import SecurityBase
from raml_document import SecuredBy, SecurityScheme, Yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

    from raml_document.from_pydantic import Walk

__all__ = ['Security', 'requirements']

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


@dataclass(slots=True)
class Security:
    """The schemes the routes run, each declared once under a name of its own.

    `declared` is `securitySchemes:`, filled as routes are read: a scheme is
    declared the first time a route runs it, so one no route runs is not.
    """

    walk: Walk
    declared: dict[str, SecurityScheme] = field(default_factory=dict)
    #: A scheme's configuration -> the name it was declared under, or `None`
    #: where it has no RAML form.
    _names: dict[tuple[str, str, str], str | None] = field(default_factory=dict)

    def name(self, scheme: SecurityBase) -> str | None:
        """The name `scheme` is declared under, declaring it on first sight.

        Two schemes configured alike are one, whichever instance a route holds.
        Two configured differently are two, even under one `scheme_name` --
        which FastAPI defaults to the class name, so two `APIKeyHeader`s for
        different headers share it -- and the second is renamed and reported,
        rather than described as the first.
        """
        signature = _signature(scheme)
        if signature in self._names:
            return self._names[signature]
        built = _scheme(scheme, self.walk)
        name: str | None = None
        if built is not None:
            name = scheme.scheme_name
            if name in self.declared:
                name = next(f'{name}_{number}' for number in count(2) if f'{name}_{number}' not in self.declared)
                self.walk.drop(
                    'securitySchemes', f'two schemes are named {scheme.scheme_name!r}; the second is declared as {name}'
                )
            self.declared[name] = built
        self._names[signature] = name
        return name

    def secured_by(self, route: Any, at: str) -> list[SecuredBy]:
        """`securedBy:` for one route: each scheme it runs that could be declared.

        A scheme that could not be declared is left out, since a `securedBy`
        naming an undeclared scheme does not parse; its drop already says why.
        One scheme reached twice, with different scopes, is one entry with
        both. Where none of them refuses a request without its credential
        (`auto_error=False`), the route also answers anonymously, which RAML
        writes as a `null` entry.
        """
        merged: dict[str, list[str]] = {}
        enforcing: list[str] = []
        for scheme, scopes in requirements(route.dependant):
            name = self.name(scheme)
            if name is None:
                continue
            if name not in merged and getattr(scheme, 'auto_error', True):
                enforcing.append(name)
            known = merged.setdefault(name, [])
            known.extend(scope for scope in scopes if scope not in known)
        if len(enforcing) > 1:
            # FastAPI runs every dependency, and each of these refuses a request
            # without its credential; RAML reads a `securedBy` list as choices.
            self.walk.drop(at, f'{", ".join(enforcing)} are each required, and RAML reads securedBy as alternatives')
        out: list[SecuredBy] = []
        for name, scopes in merged.items():
            declared = self.declared[name]
            oauth = declared.type == 'OAuth 2.0'
            if scopes and not oauth:
                self.walk.drop(at, f'{name!r} carries scopes {scopes}, and RAML scopes belong to OAuth 2.0')
            if oauth:
                _declare_scopes(declared, scopes)
            out.append(SecuredBy(scheme=name, scopes=scopes if oauth else []))
        if merged and not enforcing:
            out.append(SecuredBy(scheme=None))
        return out


def _signature(scheme: SecurityBase) -> tuple[str, str, str]:
    """What makes two scheme instances the same scheme: their name, kind and configuration."""
    dump = getattr(scheme.model, 'model_dump', None)
    config = json.dumps(dump(mode='json', exclude_none=True), sort_keys=True) if dump else repr(vars(scheme.model))
    return scheme.scheme_name, type(scheme).__qualname__, config


def _declare_scopes(declared: SecurityScheme, scopes: list[str]) -> None:
    """Add to an OAuth scheme's `scopes` each one a route asks for that it does not list.

    FastAPI lets a route ask for any scope; RAML refuses a `securedBy` scope
    its scheme does not declare. A scope asked for is one the scheme grants.
    """
    if not scopes:
        return
    listed = declared.settings.setdefault('scopes', [])
    assert isinstance(listed, list)  # noqa: S101 - `_oauth2` writes a list
    listed.extend(scope for scope in scopes if scope not in listed)


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
        settings['scopes'] = list(dict.fromkeys(scopes))
    return SecurityScheme(type='OAuth 2.0', description=description, settings=settings)
