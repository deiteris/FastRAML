"""Which routes an app describes, and the `(verb, handler)` pairs each one stands for."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import hdrs

if TYPE_CHECKING:
    from raml_document.from_pydantic import Walk

__all__ = ['operations']


def operations(route: Any, walk: Walk, path: str) -> list[tuple[str, Any]]:
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
