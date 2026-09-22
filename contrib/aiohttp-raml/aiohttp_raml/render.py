"""Read an application and build a `Document`.

Everything comes off what the handlers declared. `validate` left a `Described`
on each wrapper -- the same record the injector validates against -- so a
parameter cannot be documented in one place and read from another.

Routes come from `app.router.resources()`; a resource's `get_info()` gives a
`path` or a `formatter`, and the formatter already spells `{name}` as RAML does.

Anything the renderer cannot express lands in `Report.dropped`, never omitted in
silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import hdrs
from pydantic.fields import FieldInfo
from raml_document import (
    Body,
    Document,
    Documentation,
    Method,
    Parameters,
    Resource,
    Response,
    SecuredBy,
    SecurityScheme,
    TypeDecl,
)
from raml_document.from_pydantic import Walk

from aiohttp_raml.decorator import Described, described, excluded
from aiohttp_raml.multipart import File
from aiohttp_raml.params import BODY, HEADER, MISSING, QUERY, URI, Declared
from aiohttp_raml.security import AUTH_SCHEMES

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ['Report', 'render']

#: The `Method` field each RAML node is rendered into.
_NODES = {QUERY: 'query_parameters', HEADER: 'headers'}


@dataclass(slots=True)
class Report:
    """A rendered document, and everything the renderer could not express."""

    document: Document
    dropped: list[str] = field(default_factory=list)

    def to_raml(self) -> str:
        return self.document.to_raml()


def _field_info(item: Declared) -> FieldInfo:
    """One declaration -> the `FieldInfo` a model property would be."""
    if item.default is MISSING:
        return FieldInfo.from_annotation(item.annotation)
    return FieldInfo.from_annotated_attribute(item.annotation, item.default)


def _parameters(entry: Described, method: Method, at: str, walk: Walk) -> Parameters:
    """Split the declarations into RAML's nodes; return the URI ones."""
    uri: Parameters = {}
    for item in entry.bound.declared:
        if item.place == BODY:
            continue
        info = _field_info(item)
        decl = walk.optional(walk.field(info, f'{at}.{item.name}'), info)
        if item.place == URI:
            # A URI parameter is part of the path: the route matched, so it is
            # there. `required: false` on one contradicts the path it sits in.
            decl.required = None
            uri[item.wire] = decl
        else:
            getattr(method, _NODES[item.place])[item.wire] = decl
    return uri


def _body(entry: Described, method: Method, at: str, walk: Walk) -> None:
    if entry.bound.parts:
        method.body = Body({'multipart/form-data': _form(entry.bound.parts, at, walk)})
        return
    item = next((one for one in entry.bound.declared if one.place == BODY), None)
    if item is not None:
        method.body = Body({item.media: walk.annotation(item.annotation, f'{at}.body')})


def _form(parts: list[Declared], at: str, walk: Walk) -> TypeDecl:
    """A multipart body: one object whose properties are the form's fields."""
    properties: Parameters = {}
    for item in parts:
        decl = _file(item.file or File()) if item.is_file else walk.annotation(item.annotation, f'{at}.{item.wire}')
        if not item.required:
            decl.required = False
        properties[item.wire] = decl
    return TypeDecl(type='object', properties=properties)


def _file(facets: File) -> TypeDecl:
    """RAML's `file` type, and the three facets it carries.

    `minLength` and `maxLength` are bytes on this kind, which is why the sizes
    render into the same two facets a string uses for characters.
    """
    return TypeDecl(
        type='file',
        description=facets.description,
        file_types=[*facets.file_types] if facets.file_types else None,
        min_length=facets.min_size,
        max_length=facets.max_size,
    )


def _place_uri(root: Resource, path: str, uri: Parameters, at: str, walk: Walk) -> None:
    """Put each URI parameter on the resource whose segment names it.

    Not on the leaf. `/books/{isbn}/cover` nests as `/books`, `/{isbn}`,
    `/cover`, and `isbn` belongs to the middle one -- RAML requires a
    `uriParameters` entry to name a template in *that* resource's relative URI,
    and rejects the document otherwise. It only looks equivalent while the
    parameter happens to be the last segment.
    """
    remaining = dict(uri)
    prefix = ''
    for segment in (part for part in path.split('/') if part):
        prefix = f'{prefix}/{segment}'
        if segment.startswith('{') and segment.endswith('}'):
            declaration = remaining.pop(segment[1:-1], None)
            if declaration is not None:
                root.at(prefix).uri_parameters[segment[1:-1]] = declaration
    for name in remaining:
        walk.drop(at, f'uri parameter {name!r} names no segment of {path}; not written')


def _responses(entry: Described, method: Method, at: str, walk: Walk) -> None:
    for declared in entry.bound.declared_responses:
        response = Response(description=declared.description)
        if declared.body is not None:
            response.body = Body({declared.media: walk.annotation(declared.body, f'{at}.{declared.code}')})
        method.responses[declared.code] = response


def _secured_by(entry: Described, declared: dict[str, SecurityScheme], at: str, walk: Walk) -> list[SecuredBy]:
    out: list[SecuredBy] = []
    for name, scopes in entry.secured_by:
        scheme = declared.get(name)
        if scheme is None:
            walk.drop(at, f'securedBy {name!r} is not written: no scheme of that name is registered')
            continue
        if scopes and scheme.type != 'OAuth 2.0':
            walk.drop(at, f'{name!r} carries scopes {sorted(scopes)}, and RAML scopes belong to OAuth 2.0')
            out.append(SecuredBy(scheme=name))
            continue
        out.append(SecuredBy(scheme=name, scopes=list(scopes)))
    return out


def _method(
    entry: Described, verb: str, path: str, declared: dict[str, SecurityScheme], walk: Walk
) -> tuple[Method, Parameters]:
    at = f'{verb} {path}'
    method = Method(display_name=entry.display_name, description=entry.description)
    uri = _parameters(entry, method, at, walk)
    _body(entry, method, at, walk)
    _responses(entry, method, at, walk)
    method.secured_by.extend(_secured_by(entry, declared, at, walk))
    return method, uri


def _operations(route: Any, walk: Walk, path: str) -> list[tuple[str, Any]]:
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


def _security(app: Any) -> dict[str, SecurityScheme]:
    """`securitySchemes:`, from what `security.setup` registered.

    Every registered scheme is declared, used or not: that is what the app says
    about itself, and RAML permits a declaration nothing refers to.
    """
    registry = app.get(AUTH_SCHEMES) or {}
    return {name: scheme.declare() for name, scheme in registry.items()}


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
    document.security_schemes = _security(app)

    for entry in app.router.resources():
        if excluded(entry):
            continue
        info = entry.get_info()
        path = info.get('path') or info.get('formatter')
        if path is None:
            walk.drop(str(entry), 'a resource with no path is not an API operation; not described')
            continue
        built: dict[str, Method] = {}
        uri: Parameters = {}
        for route in entry:
            if excluded(route.handler):
                continue
            for verb, handler in _operations(route, walk, path):
                if excluded(handler):
                    continue
                found = described(handler)
                if found is None:
                    walk.drop(f'{verb} {path}', 'handler is not decorated with @validate; described by its path alone')
                    built[verb.lower()] = Method()
                    continue
                method, declared = _method(found, verb, path, document.security_schemes, walk)
                built[verb.lower()] = method
                # Merged across the verbs: they share the path, so they share
                # its parameters, and writing them once per verb would write the
                # same declaration onto the same resource several times.
                uri.update(declared)
        # Only now: `at` creates every segment on the way, so asking for the
        # path of a resource whose every handler is excluded would leave an
        # empty node behind.
        if not built:
            continue
        document.root.at(path).methods.update(built)
        _place_uri(document.root, path, uri, path, walk)

    # Last: the walk registers models as the handlers are read, so `types` is
    # only complete once every handler has been.
    document.types = walk.types
    return Report(document=document, dropped=walk.dropped)
