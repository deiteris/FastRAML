"""Read a FastAPI application and build a `Document`.

Two sources, both read straight off the app.

**The models**, through `raml_document.from_pydantic.Walk`, which reads
`model_fields` and the annotations. Not through `model_json_schema()`: that is a
projection built for a different target and drops things RAML can carry --
`Decimal` precision, a `dict` key type, an integer discriminator tag.

**The routes**, off each `APIRoute`: `path_format` already uses `{param}` as RAML
does, the dependency tree holds the path, query and header parameters and the
security schemes, and `body_field` and `response_field` carry the payloads.

The two meet at `FieldInfo`, which is what FastAPI gives for a parameter and what
pydantic gives for a model field -- so a query parameter and a property go
through one function and pick up the same constraints.

**Only public FastAPI names.** The helpers FastAPI's own OpenAPI generator uses
are private and have moved between releases; reading the `Dependant` tree
directly works on every release `pyproject.toml` admits.

Anything the renderer cannot express lands in `Report.dropped` rather than being
omitted in silence.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from fastapi import routing as fastapi_routing
from fastapi.datastructures import DefaultPlaceholder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security.base import SecurityBase
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import BaseModel
from raml_document import (
    UNSET,
    Body,
    Document,
    Method,
    Parameters,
    Response,
    SecuredBy,
    SecurityScheme,
    TypeDecl,
    Yaml,
)
from raml_document.from_pydantic import Walk
from starlette.datastructures import UploadFile
from starlette.routing import Mount

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['Report', 'render', 'routes_of']

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
#: What FastAPI's default response class says when a route says nothing.
DEFAULT_RESPONSE_DESCRIPTION: Final = 'Successful Response'


@dataclass(slots=True)
class Report:
    """A rendered document, and everything the renderer could not express."""

    document: Document
    dropped: list[str] = field(default_factory=list)

    def to_raml(self) -> str:
        return self.document.to_raml()


# -- routes -------------------------------------------------------------------


def routes_of(app: Any) -> list[Any]:
    """Every route the app serves, an included router's routes among them.

    FastAPI 0.139 stopped copying an included router's routes into
    `app.routes`: `include_router` adds one entry that stands for the router, so
    a route added to it later is served too. `iter_route_contexts` reads through
    those entries with the prefix and dependencies applied -- the walk
    `get_openapi` makes. Before it existed `app.routes` was already flat.

    Each item is the route itself, or a context that answers every attribute a
    route does; `original_route` on a context is the object `add_api_route`
    built, which is what stays the same between two calls.
    """
    iterate = getattr(fastapi_routing, 'iter_route_contexts', None)
    return list(iterate(app.routes)) if iterate is not None else list(app.routes)


def _api_routes(app: Any, walk: Walk) -> list[Any]:
    """The routes to describe: the app's own operations, in the schema."""
    routes: list[Any] = []
    for route in routes_of(app):
        original = getattr(route, 'original_route', route)
        if isinstance(original, APIRoute):
            if route.include_in_schema:
                routes.append(route)
        elif isinstance(original, Mount) and original.routes:
            # A mounted FastAPI app is an app of its own, with its own schema
            # -- `get_openapi` leaves it out too. A mount with no routes is
            # static files, the viewer among them, and says nothing.
            walk.drop(original.path, 'a mounted application is not described; give it add_raml_routes of its own')
    return routes


# -- security -----------------------------------------------------------------


def _requirements(dependant: Any) -> Iterator[tuple[SecurityBase, list[str]]]:
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
        yield from _requirements(sub)


def _security(routes: list[Any], walk: Walk) -> dict[str, SecurityScheme]:
    """`securitySchemes:`, harvested off each route's dependency tree."""
    schemes: dict[str, SecurityScheme] = {}
    for route in routes:
        for scheme, _ in _requirements(route.dependant):
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


def _secured_by(route: Any, schemes: dict[str, SecurityScheme], at: str, walk: Walk) -> list[SecuredBy]:
    """`securedBy:` for one route: each scheme it runs that the document declares.

    A scheme that could not be declared is left out, since a `securedBy`
    naming an undeclared scheme does not parse; its drop already says why. One
    scheme reached twice, with different scopes, is one entry with both.
    """
    merged: dict[str, list[str]] = {}
    enforcing: list[str] = []
    for scheme, scopes in _requirements(route.dependant):
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


# -- parameters ---------------------------------------------------------------


def _declared(dependant: Any) -> dict[str, list[Any]]:
    """Every parameter the dependency tree declares, by where it travels, in FastAPI's order."""
    found: dict[str, list[Any]] = {'path': [], 'query': [], 'header': [], 'cookie': []}
    pending = [dependant]
    while pending:
        current = pending.pop()
        for place, params in found.items():
            params.extend(getattr(current, f'{place}_params'))
        pending.extend(reversed(current.dependencies))
    return found


def _flattened(place: str, params: list[Any], at: str, walk: Walk) -> Iterator[tuple[str, Any]]:
    """`(wire name, FieldInfo)` for each parameter, a parameter model opened up.

    A lone parameter whose type is a model -- `Annotated[Filters, Query()]` --
    is FastAPI's parameter model, and its fields are the parameters. A header
    field is named as it travels, with the underscores its `Header()` converts.
    """
    first = params[0].field_info if len(params) == 1 else None
    model = first.annotation if first is not None else None
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        for param in params:
            yield param.alias, param.field_info
        return
    if model.model_config.get('extra') == 'forbid':
        walk.drop(at, f'{model.__name__} refuses {place} parameters it does not declare, which RAML cannot say')
    convert = getattr(first, 'convert_underscores', True)
    for name, info in model.model_fields.items():
        wire = info.alias or name
        if place == 'header' and convert and info.alias is None:
            wire = wire.replace('_', '-')
        yield wire, info


def _parameters(route: Any, method: Method, at: str, walk: Walk) -> Parameters:
    """Put the route's query and header parameters on `method`; return the path ones."""
    uri: Parameters = {}
    targets = {'path': uri, 'query': method.query_parameters, 'header': method.headers}
    for place, params in _declared(route.dependant).items():
        if not params:
            continue
        for wire, info in _flattened(place, params, at, walk):
            if place == 'cookie':
                walk.drop(at, f'cookie parameter {wire!r} has no RAML form')
                continue
            # A parameter is text or absent: `None` in its annotation means
            # only that it may be left out, never that the wire carries a null.
            targets[place][wire] = walk.optional(walk.field(info, f'{at}.{wire}', nullable=False), info)
    return uri


# -- bodies and responses -------------------------------------------------------


def _response_class(route: Any) -> type:
    """The route's response class, unwrapped from FastAPI's `Default(...)` marker."""
    response_class = route.response_class
    if isinstance(response_class, DefaultPlaceholder):
        response_class = response_class.value
    return response_class  # type: ignore[no-any-return]


def _payload(route: Any, info: Any, at: str, walk: Walk) -> TypeDecl:
    """A response body's type: what the model *writes*, when FastAPI writes by alias."""
    if getattr(route, 'response_model_by_alias', True):
        with walk.output():
            return walk.field(info, at)
    return walk.field(info, at)


def _responses(route: Any, method: Method, at: str, walk: Walk) -> None:
    """The route's own response, then each one `responses=` adds."""
    response_class = _response_class(route)
    media = getattr(response_class, 'media_type', None)
    code = route.status_code or 200
    response = method.responses.setdefault(code, Response())
    if route.response_description != DEFAULT_RESPONSE_DESCRIPTION:
        response.description = route.response_description
    if is_body_allowed_for_status_code(code):
        response.body = _route_body(route, response_class, media, f'{at}.{code}', walk)

    for key, spec in (route.responses or {}).items():
        # FastAPI accepts `"default"` and a range such as `"4XX"`. RAML keys a
        # response by its status code and has neither.
        if not (isinstance(key, int) or (isinstance(key, str) and key.isdigit())):
            what = 'a default response' if key == 'default' else f'the status range {key!r}'
            walk.drop(at, f'{what} has no RAML form; a response is keyed by its status code')
            continue
        extra = method.responses.setdefault(int(key), Response())
        if spec.get('description'):
            extra.description = spec['description']
        # Joined to what the route's own response already says, not over it:
        # `responses={200: {'content': {'image/png': {}}}}` adds a media type.
        body = extra.body or Body()
        for extra_media, entry in (spec.get('content') or {}).items():
            if (entry or {}).get('schema'):
                walk.drop(f'{at}.{key}', f'a JSON Schema for {extra_media} is not translated; written as any')
            body.content.setdefault(extra_media, TypeDecl(type='any'))
        if spec.get('model') is not None:
            # FastAPI puts a `model` under the route's own media type.
            with walk.output():
                body.content[media or 'application/json'] = walk.annotation(spec['model'], f'{at}.{key}')
        if spec.get('headers'):
            walk.drop(f'{at}.{key}', 'response headers are not described')
        if body.content:
            extra.body = body


def _route_body(route: Any, response_class: type, media: str | None, at: str, walk: Walk) -> Body | None:
    """The body of the route's own response, as FastAPI's schema would state it."""
    if getattr(route, 'is_json_stream', False) or getattr(route, 'is_sse_stream', False):
        stream = 'application/jsonl' if route.is_json_stream else 'text/event-stream'
        walk.drop(at, f'a {stream} stream of items has no RAML form; written as any')
        return Body({stream: TypeDecl(type='any')})
    if media is None:
        return None
    if not issubclass(response_class, JSONResponse):
        # `HTMLResponse`, `PlainTextResponse`: text, whatever the handler's
        # annotation says it builds the text from.
        return Body({media: TypeDecl(type='string')})
    if route.response_field is None:
        return Body({media: TypeDecl(type='any')})
    return Body({media: _payload(route, route.response_field.field_info, at, walk)})


def _method(route: Any, verb: str, schemes: dict[str, SecurityScheme], walk: Walk) -> tuple[Method, Parameters]:
    at = f'{verb} {route.path_format}'
    method = Method(display_name=route.summary or None, description=route.description or None)
    uri = _parameters(route, method, at, walk)

    if route.body_field is not None:
        info = route.body_field.field_info
        media = getattr(info, 'media_type', None) or 'application/json'
        method.body = Body({media: walk.field(info, f'{at}.body')})

    _responses(route, method, at, walk)
    method.secured_by = _secured_by(route, schemes, at, walk)
    if route.callbacks:
        walk.drop(at, 'callbacks have no RAML form')
    return method, uri


def render(app: Any) -> Report:
    """Render `app` as a RAML 1.0 document."""
    # `UploadFile` is Starlette's; RAML's `file` is what it carries.
    walk = Walk(scalars={UploadFile: 'file'})
    document = Document(title=app.title, version=app.version or None, description=app.description or None)
    if app.servers:
        document.base_uri = app.servers[0]['url']
        for name, spec in (app.servers[0].get('variables') or {}).items():
            document.base_uri_parameters[name] = TypeDecl(type='string', default=spec.get('default', UNSET))
        if len(app.servers) > 1:
            walk.drop('servers', f'{len(app.servers) - 1} extra server(s); RAML has one baseUri')

    routes = _api_routes(app, walk)
    document.security_schemes = _security(routes, walk)

    for route in routes:
        path = route.path_format
        resource = document.root.at(path)
        for verb in sorted(route.methods or ()):
            method, uri = _method(route, verb, document.security_schemes, walk)
            resource.methods[verb.lower()] = method
            for name, decl in uri.items():
                if not document.root.declare_uri_parameter(path, name, decl):
                    walk.drop(f'{verb} {path}', f'path parameter {name!r} names no segment of the path')

    # Last: the walk registers models as the routes are read, so `types` is only
    # complete once every route has been.
    document.types = walk.types
    return Report(document=document, dropped=walk.dropped)
