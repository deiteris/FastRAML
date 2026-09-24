"""Read a FastAPI application and build a `Document`.

Two sources, both read straight off the app.

**The models**, through `raml_document.from_pydantic.Walk`, which reads
`model_fields` and the annotations. Not through `model_json_schema()`: that is a
projection built for a different target and drops things RAML can carry --
`Decimal` precision, a `dict` key type, an integer discriminator tag.

**The routes**, off each `APIRoute`: `path_format` already uses `{param}` as RAML
does, `get_flat_params` splits path from query from header, `body_field` and
`response_field` carry the payload annotations, and security comes off the
dependency tree.

The two meet at `FieldInfo`, which is what FastAPI gives for a parameter and what
pydantic gives for a model field -- so a query parameter and a property go
through one function and pick up the same constraints.

Anything the renderer cannot express lands in `Report.dropped` rather than being
omitted in silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from fastapi.datastructures import DefaultPlaceholder
from fastapi.dependencies.models import (
    _get_oauth_scopes,
    _get_security_scheme,
    _is_security_scheme,
)
from fastapi.dependencies.utils import get_flat_params
from fastapi.routing import APIRoute
from raml_document import (
    UNSET,
    Body,
    Document,
    Method,
    Parameters,
    Resource,
    Response,
    SecuredBy,
    SecurityScheme,
    TypeDecl,
    Yaml,
)
from raml_document.from_pydantic import Walk

__all__ = ['Report', 'render']

#: An OAuth2 flow as FastAPI names it -> as RAML names it.
GRANTS: Final[dict[str, str]] = {
    'authorizationCode': 'authorization_code',
    'clientCredentials': 'client_credentials',
    'password': 'password',
    'implicit': 'implicit',
}
#: The `Method` field each parameter kind belongs to, by the class FastAPI wraps
#: it in. `Path` is absent because a path parameter belongs to the *resource*.
BUCKETS: Final[dict[str, str]] = {
    'Path': 'uri_parameters',
    'Query': 'query_parameters',
    'Header': 'headers',
}


@dataclass(slots=True)
class Report:
    """A rendered document, and everything the renderer could not express."""

    document: Document
    dropped: list[str] = field(default_factory=list)

    def to_raml(self) -> str:
        return self.document.to_raml()


def _payload(annotation: Any, media: str, walk: Walk, at: str) -> Body:
    """One body. The annotation is walked, so `list[Book]` becomes `Book[]`."""
    return Body({media: walk.annotation(annotation, at)})


def _security(routes: list[APIRoute], walk: Walk) -> dict[str, SecurityScheme]:
    """`securitySchemes:`, harvested off each route's dependency tree."""
    schemes: dict[str, SecurityScheme] = {}

    def visit(dependant: Any) -> None:
        if _is_security_scheme(dependant=dependant):
            scheme = _get_security_scheme(dependant=dependant)
            model = scheme.model.model_dump(exclude_none=True)
            kind = getattr(model['type_'], 'value', model['type_'])
            built = _scheme(kind, model, scheme.scheme_name, walk)
            if built is not None:
                schemes[scheme.scheme_name] = built
        for sub in dependant.dependencies:
            visit(sub)

    for route in routes:
        visit(route.dependant)
    return schemes


def _scheme(kind: str, model: dict[str, Any], name: str, walk: Walk) -> SecurityScheme | None:
    if kind == 'oauth2':
        flow, config = next(iter(model['flows'].items()))
        settings: dict[str, Yaml] = {'authorizationGrants': [GRANTS.get(flow, flow)]}
        if config.get('authorizationUrl'):
            settings['authorizationUri'] = config['authorizationUrl']
        if config.get('tokenUrl'):
            settings['accessTokenUri'] = config['tokenUrl']
        if config.get('scopes'):
            settings['scopes'] = sorted(config['scopes'])
        return SecurityScheme(type='OAuth 2.0', settings=settings)
    if kind == 'http' and model.get('scheme') == 'basic':
        return SecurityScheme(type='Basic Authentication')
    if kind == 'http' and model.get('scheme') == 'digest':
        return SecurityScheme(type='Digest Authentication')
    if kind == 'apiKey':
        where = {'header': 'headers', 'query': 'queryParameters'}.get(model.get('in_', ''))
        if where is None:
            walk.drop(name, f'apiKey in {model.get("in_")!r} has no RAML form')
            return None
        return SecurityScheme(type='Pass Through', described_by={where: {model['name']: {'type': 'string'}}})
    walk.drop(name, f'security scheme kind {kind!r} has no RAML form')
    return None


def _secured_by(dependant: Any, out: list[SecuredBy]) -> None:
    if _is_security_scheme(dependant=dependant):
        out.append(
            SecuredBy(
                scheme=_get_security_scheme(dependant=dependant).scheme_name,
                scopes=_get_oauth_scopes(dependant=dependant),
            )
        )
    for sub in dependant.dependencies:
        _secured_by(sub, out)


def _parameters(route: APIRoute, method: Method, at: str, walk: Walk) -> Parameters:
    """Split the route's parameters into RAML's three places; return the path ones."""
    uri: Parameters = {}
    buckets: dict[str, Parameters] = {
        'uri_parameters': uri,
        'query_parameters': method.query_parameters,
        'headers': method.headers,
    }
    for param in get_flat_params(route.dependant):
        kind = type(param.field_info).__name__
        target = BUCKETS.get(kind)
        if target is None:
            walk.drop(at, f'{kind} parameter {param.alias!r} has no RAML form')
            continue
        # A FastAPI parameter *is* a `FieldInfo`, so it walks exactly as a model
        # field does and picks up the same constraints.
        buckets[target][param.alias] = walk.field(param.field_info, f'{at}.{param.alias}')
    return uri


def _responses(route: APIRoute, method: Method, at: str, walk: Walk) -> None:
    if route.response_field is not None:
        # `response_class` is a DefaultPlaceholder unless the route set one, and
        # the real class is behind `.value` -- the unwrap FastAPI itself does.
        response_class = route.response_class
        if isinstance(response_class, DefaultPlaceholder):
            response_class = response_class.value
        media = response_class.media_type or 'application/json'
        annotation = route.response_field.field_info.annotation
        code = route.status_code or 200
        method.responses[code] = Response(body=_payload(annotation, media, walk, f'{at}.{code}'))

    for key, spec in (route.responses or {}).items():
        # FastAPI accepts a wildcard range as a string key -- `"4XX"` -- and RAML
        # has no spelling for one: a response key is a status code.
        if not (isinstance(key, int) or (isinstance(key, str) and key.isdigit())):
            walk.drop(at, f'response key {key!r} is a range, and RAML keys responses by status code')
            continue
        response = method.responses.setdefault(int(key), Response())
        if spec.get('description'):
            response.description = spec['description']
        if spec.get('model') is not None:
            response.body = _payload(spec['model'], 'application/json', walk, f'{at}.{key}')


def _method(route: APIRoute, verb: str, walk: Walk) -> tuple[Method, Parameters]:
    at = f'{verb} {route.path_format}'
    method = Method(display_name=route.summary or None, description=route.description or None)
    uri = _parameters(route, method, at, walk)

    if route.body_field is not None:
        info = route.body_field.field_info
        media = getattr(info, 'media_type', None) or 'application/json'
        method.body = _payload(info.annotation, media, walk, f'{at}.body')

    _responses(route, method, at, walk)
    _secured_by(route.dependant, method.secured_by)
    if route.callbacks:
        walk.drop(at, 'callbacks have no RAML form')
    return method, uri


def render(app: Any) -> Report:
    """Render `app` as a RAML 1.0 document."""
    walk = Walk()
    document = Document(title=app.title, version=app.version or None, description=app.description or None)
    if app.servers:
        document.base_uri = app.servers[0]['url']
        for name, spec in (app.servers[0].get('variables') or {}).items():
            document.base_uri_parameters[name] = TypeDecl(type='string', default=spec.get('default', UNSET))
        if len(app.servers) > 1:
            walk.drop('servers', f'{len(app.servers) - 1} extra server(s); RAML has one baseUri')

    routes = [route for route in app.routes if isinstance(route, APIRoute) and route.include_in_schema]
    document.security_schemes = _security(routes, walk)

    for route in routes:
        resource: Resource = document.root.at(route.path_format)
        for verb in sorted(route.methods or ()):
            method, uri = _method(route, verb, walk)
            resource.methods[verb.lower()] = method
            resource.uri_parameters.update(uri)

    # Last: the walk registers models as the routes are read, so `types` is only
    # complete once every route has been.
    document.types = walk.types
    return Report(document=document, dropped=walk.dropped)
