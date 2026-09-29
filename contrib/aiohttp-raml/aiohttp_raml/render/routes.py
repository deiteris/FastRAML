"""Which routes an app describes, and the `(verb, handler)` pairs each one stands for."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import hdrs

from aiohttp_raml.decorator import excluded

if TYPE_CHECKING:
    from collections.abc import Iterator

    from raml_document.from_pydantic import Walk

__all__ = ['apps', 'operations', 'resources']

#: The applications a resource is mounted under, outermost first: the app
#: itself, then each sub-application down to the one that routes it.
Chain = tuple[Any, ...]


def apps(app: Any, chain: Chain = ()) -> Iterator[Chain]:
    """`app` and every sub-application mounted under it, each as the chain that reaches it."""
    chain = (*chain, app)
    yield chain
    for entry in app.router.resources():
        info = entry.get_info()
        if not excluded(entry) and 'prefix' in info and 'app' in info:
            yield from apps(info['app'], chain)


def resources(app: Any, walk: Walk, chain: Chain = ()) -> Iterator[tuple[Chain, str, Any]]:
    """Every resource the app routes to -- a sub-application's among them -- with its path.

    `add_subapp` prefixes each of the sub-application's resources as it mounts
    them, so their paths are already whole. A sub-application matched by the
    request's host (`add_domain`) is not under the document's one `baseUri`,
    and a resource with no path -- a static directory -- is no API operation;
    both are reported.
    """
    chain = (*chain, app)
    for entry in app.router.resources():
        if excluded(entry):
            continue
        info = entry.get_info()
        if 'app' in info:
            if 'prefix' in info:
                yield from resources(info['app'], walk, chain)
            else:
                walk.drop(str(entry), 'a sub-application matched by host, and RAML has one baseUri; not described')
            continue
        path = info.get('path') or info.get('formatter')
        if path is None:
            walk.drop(str(entry), 'a resource with no path is not an API operation; not described')
            continue
        yield chain, path, entry


def operations(resource: Any, walk: Walk, path: str) -> list[tuple[str, Any]]:
    """The `(verb, handler)` pairs one resource describes, leaving out what `exclude` marks.

    `add_get` also routes HEAD to the GET handler, which is what HTTP says a
    HEAD is -- a GET without its body -- so that HEAD says nothing a reader of
    `get:` does not already know, and would describe a body a HEAD never has.
    A HEAD with a handler of its own is described.
    """
    routes = [route for route in resource if not excluded(route.handler)]
    gets = {route.handler for route in routes if route.method == hdrs.METH_GET}
    out: list[tuple[str, Any]] = []
    for route in routes:
        if route.method == hdrs.METH_HEAD and route.handler in gets:
            continue
        out.extend(pair for pair in _verbs(route, walk, path) if not excluded(pair[1]))
    return out


def _verbs(route: Any, walk: Walk, path: str) -> list[tuple[str, Any]]:
    """The `(verb, handler)` pairs one route describes.

    A class-based route carries the method `*`, and the verbs it answers are the
    ones the class defines. A *function* registered for `*` names no verbs at
    all, and RAML has no wildcard method -- `*:` is not a node a parser accepts
    -- so there is nothing to write and the route is reported instead.
    """
    handler = route.handler
    if not isinstance(handler, type):
        if route.method == hdrs.METH_ANY:
            walk.drop(path, 'a handler registered for every method names none, and RAML has no wildcard method')
            return []
        return [(route.method, handler)]
    if route.method != hdrs.METH_ANY:
        return [(route.method, getattr(handler, route.method.lower()))]
    verbs = sorted(verb for verb in hdrs.METH_ALL if hasattr(handler, verb.lower()))
    if not verbs:
        walk.drop(path, f'{handler.__name__} defines no HTTP method')
    return [(verb, getattr(handler, verb.lower())) for verb in verbs]
