"""Which routes an app serves, and which of them the document describes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import routing as fastapi_routing
from fastapi.routing import APIRoute
from starlette.routing import Mount

if TYPE_CHECKING:
    from raml_document.from_pydantic import Walk

__all__ = ['api_routes', 'routes_of']


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


def api_routes(app: Any, walk: Walk) -> list[Any]:
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
