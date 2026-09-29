"""A handler's request: its URI parameters, query parameters, headers and body."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from raml_document import Body, Parameters, TypeDecl

from aiohttp_raml.multipart import File
from aiohttp_raml.params import BODY, HEADER, QUERY, URI
from aiohttp_raml.render.routes import crosses_segments

if TYPE_CHECKING:
    from raml_document import Method
    from raml_document.from_pydantic import Walk

    from aiohttp_raml.decorator import Described
    from aiohttp_raml.params import Declared

__all__ = ['body', 'parameters', 'segment_constraints']

#: The `Method` field each RAML node is rendered into.
NODES: Final = {QUERY: 'query_parameters', HEADER: 'headers'}


def parameters(entry: Described, method: Method, at: str, walk: Walk) -> Parameters:
    """Split the declarations into RAML's nodes; return the URI ones."""
    uri: Parameters = {}
    for item in entry.bound.declared:
        if item.place == BODY:
            continue
        decl = walk.parameter(item.info, f'{at}.{item.name}')
        if item.place == URI:
            # A URI parameter is part of the path: the route matched, so it is
            # there. `required: false` on one contradicts the path it sits in.
            decl.required = None
            uri[item.wire] = decl
        else:
            getattr(method, NODES[item.place])[item.wire] = decl
    return uri


def segment_constraints(uri: Parameters, patterns: dict[str, str], at: str, walk: Walk) -> None:
    """Write each regex the route states for a segment as its URI parameter's `pattern`.

    `{isbn:[0-9]{13}}` refuses a request whose segment does not match, so the
    parameter is declared with that pattern -- a string one, if the handler
    does not declare it. A regex that can match `/` spans segments, which a RAML
    URI parameter cannot, and is reported like any other loss.
    """
    for name, regex in patterns.items():
        if crosses_segments(regex):
            walk.drop(at, f'{{{name}}} matches {regex}, which crosses "/", and a RAML URI parameter is one segment')
            continue
        decl = uri.setdefault(name, TypeDecl(type='string'))
        if decl.type != 'string':
            walk.drop(at, f'{name}: the route matches {regex}, and RAML has no pattern facet for type {decl.type}')
        elif decl.pattern is not None:
            walk.drop(at, f'{name}: the route matches {regex} beside the pattern declared, and RAML states one')
        else:
            # Anchored: RAML's `pattern` is a search, and the route's regex
            # matches the whole segment.
            decl.pattern = f'^(?:{regex})$'


def body(entry: Described, method: Method, at: str, walk: Walk) -> None:
    if entry.bound.parts:
        method.body = Body({'multipart/form-data': _form(entry.bound.parts, at, walk)})
        return
    item = next((one for one in entry.bound.declared if one.place == BODY), None)
    if item is not None:
        method.body = Body({item.media: walk.annotation(item.annotation, f'{at}.body')})
        if not item.required:
            walk.drop(f'{at}.body', 'the body may be left out, and RAML has no optional body; written as required')


def _form(parts: list[Declared], at: str, walk: Walk) -> TypeDecl:
    """A multipart body: one object whose properties are the form's fields.

    A field is read like a parameter -- a part is text or absent, never null --
    so its constraints and default are wherever pydantic reads them from.
    """
    properties: Parameters = {}
    for item in parts:
        if item.is_file:
            decl = _file(item.file or File())
            if not item.required:
                decl.required = False
        else:
            decl = walk.parameter(item.info, f'{at}.{item.wire}')
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
