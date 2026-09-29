"""Read an application and build a `Document`.

Everything comes off what the handlers declared. `validate` left a `Described`
on each wrapper -- the same record the injector validates against -- so a
parameter cannot be documented in one place and read from another.

Routes come from `app.router.resources()`, and from each sub-application's in
turn; a resource's `get_info()` gives a `path` or a `formatter`, and the
formatter already spells `{name}` as RAML does.

Anything the renderer cannot express lands in `Report.dropped`, never omitted in
silence.

| Module | Decides |
|---|---|
| `routes` | which routes are described, a sub-application's among them, and the verbs each stands for |
| `parameters` | a handler's URI parameters -- with the route's own regexes -- query parameters, headers and body |
| `responses` | the responses a handler declares |
| `security` | `securitySchemes:` and each handler's `securedBy` |
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raml_document import METHODS, Document, Documentation, Method, Parameters, Report, Resource
from raml_document.from_pydantic import Walk

from aiohttp_raml.decorator import Described, described
from aiohttp_raml.render.parameters import body, parameters, segment_constraints
from aiohttp_raml.render.responses import refusals, responses
from aiohttp_raml.render.routes import apps, operations, resources, segment_patterns
from aiohttp_raml.render.security import Security

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['Report', 'render']


def _place_uri(root: Resource, path: str, uri: Parameters, at: str, walk: Walk) -> None:
    """Put each URI parameter on the resource whose segment names it (`Resource.declare_uri_parameter`)."""
    for name, declaration in uri.items():
        if not root.declare_uri_parameter(path, name, declaration):
            walk.drop(at, f'uri parameter {name!r} names no segment of {path}; not written')


def _method(
    handler: Any, entry: Described, at: str, security: Security, names: dict[str, str]
) -> tuple[Method, Parameters]:
    """One handler's method, and the URI parameters it declares for its resource to carry.

    A handler marked `@deprecated` (`warnings` or `typing_extensions`) carries
    `__deprecated__`, its message, whichever side of `@validate` it sits;
    RAML says it with the `deprecated` annotation.
    """
    walk = security.walk  # the one walk of this render, which `security` reports into as well
    method = Method(display_name=entry.display_name, description=entry.description)
    deprecated = getattr(handler, '__deprecated__', None)
    if deprecated is not None:
        walk.annotate(method.annotations, 'deprecated', str(deprecated) or None)
    uri = parameters(entry, method, at, walk)
    body(entry, method, at, walk)
    responses(entry, method, at, walk)
    method.secured_by.extend(security.secured_by(entry, names, at))
    refusals(method, at, walk)
    return method, uri


def render(  # noqa: PLR0913 - five keyword-only metadata nodes; the count is the API
    app: Any,
    *,
    title: str = 'API',
    version: str | None = None,
    description: str | None = None,
    base_uri: str | None = None,
    documentation: Sequence[Documentation] = (),
) -> Report:
    """Render `app` as a RAML 1.0 document.

    The four keyword arguments are here because a `web.Application` is a mapping
    with routes attached and carries no metadata of its own. RAML requires a
    `title`, so one is supplied rather than the document being unparseable by
    default.
    """
    walk = Walk()
    document = Document(
        title=title,
        version=version,
        description=description,
        base_uri=base_uri,
        documentation=list(documentation),
    )
    security = Security(walk)
    for chain in apps(app):
        security.names(chain)
    document.security_schemes = security.declared

    for chain, path, entry in resources(app, walk):
        names = security.names(chain)
        built: dict[str, Method] = {}
        uri: Parameters = {}
        for verb, handler in operations(entry, walk, path):
            if verb.lower() not in METHODS:
                walk.drop(f'{verb} {path}', f'RAML has no {verb} method; not described')
                continue
            found = described(handler)
            if found is None:
                walk.drop(f'{verb} {path}', 'handler is not decorated with @validate; described by its path alone')
                built[verb.lower()] = Method()
                continue
            method, declared = _method(handler, found, f'{verb} {path}', security, names)
            built[verb.lower()] = method
            # Merged across the verbs: they share the path, so they share its
            # parameters, and writing them once per verb would write the same
            # declaration onto the same resource several times.
            uri.update(declared)
        # Only now: `at` creates every segment on the way, so asking for the
        # path of a resource whose every handler is excluded would leave an
        # empty node behind.
        if not built:
            continue
        document.root.at(path).methods.update(built)
        segment_constraints(uri, segment_patterns(entry), path, walk)
        _place_uri(document.root, path, uri, path, walk)

    # Last: the walk registers models as the handlers are read, so `types` is
    # only complete once every handler has been.
    document.types = walk.types
    document.annotation_types = walk.annotation_types
    return Report(document=document, dropped=walk.dropped)
