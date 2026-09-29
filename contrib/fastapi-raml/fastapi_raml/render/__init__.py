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

| Module | Reads |
|--------|-------|
| `routes` | which routes the app serves, an included router's among them |
| `security` | the `fastapi.security` schemes the dependency trees run |
| `parameters` | path, query and header parameters, a parameter model opened up |
| `responses` | the route's own response, and what `responses=` adds |

This module composes them into one `Document`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raml_document import UNSET, Body, Document, Method, Report, TypeDecl
from raml_document.from_pydantic import Walk
from starlette.datastructures import UploadFile

from fastapi_raml.render.parameters import parameters
from fastapi_raml.render.responses import responses
from fastapi_raml.render.routes import api_routes, routes_of
from fastapi_raml.render.security import Security

if TYPE_CHECKING:
    from raml_document import Parameters

__all__ = ['Report', 'render', 'routes_of']


def _method(route: Any, verb: str, security: Security, walk: Walk) -> tuple[Method, Parameters]:
    at = f'{verb} {route.path_format}'
    method = Method(display_name=route.summary or None, description=route.description or None)
    uri = parameters(route, method, at, walk)

    if route.body_field is not None:
        info = route.body_field.field_info
        media = getattr(info, 'media_type', None) or 'application/json'
        method.body = Body({media: walk.field(info, f'{at}.body')})

    responses(route, method, at, walk)
    method.secured_by = security.secured_by(route, at)
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

    routes = api_routes(app, walk)
    security = Security(walk)
    # Filled as the routes are read, like `types`.
    document.security_schemes = security.declared

    for route in routes:
        path = route.path_format
        resource = document.root.at(path)
        for verb in sorted(route.methods or ()):
            method, uri = _method(route, verb, security, walk)
            resource.methods[verb.lower()] = method
            for name, decl in uri.items():
                if not document.root.declare_uri_parameter(path, name, decl):
                    walk.drop(f'{verb} {path}', f'path parameter {name!r} names no segment of the path')

    # Last: the walk registers models as the routes are read, so `types` is only
    # complete once every route has been.
    document.types = walk.types
    return Report(document=document, dropped=walk.dropped)
