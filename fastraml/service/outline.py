"""The outline: what a file wrote, grouped as the file groups it (docs/21 § 4).

Every entry is read from the model, through the authorship view
(`views/authored.py`), which says what a file or an entity wrote. This module
only shapes it into symbols: a section per table, a detail per entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastraml.parser.fragments import APIFragment
from fastraml.parser.security import SecuritySchemeDefinition, SecuritySchemeDescription
from fastraml.positions import Position
from fastraml.service.queries import DECLARATION_KINDS, Symbol, SymbolKind, detail_line, symbol
from fastraml.types.base import BaseShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.views import authored
from fastraml.views.render import type_name
from fastraml.yamlnode import NodeKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.parser.endpoints import Body, Operation, Request, Response
    from fastraml.parser.templates import TemplateDefinition
    from fastraml.service.workspace import Snapshot
    from fastraml.types.base import Parameter, ScalarFacet

__all__ = ['document_symbols']


def document_symbols(snapshot: Snapshot, uri: str) -> list[Symbol]:
    """The outline of `uri`: its metadata, its `uses:`, a section per
    declaration table, its documentation and its resources.

    Declarations only, as a code outline lists them: a type and its members,
    not its examples or annotations. A type's detail is its type as written,
    and an optional member is named `name?`. A section the model records no
    key for, `types:` or a method's `headers:`, spans its entries.

    What an Extension or an Overlay adds is outlined in it: a type it declared
    in the master's table, and under a master resource's path, a method it
    added there.
    """
    raml = snapshot.raml
    fragment = None if raml is None else raml.fragments.get(uri)
    if raml is None or fragment is None:
        return []
    found: list[Symbol | None] = []
    api = raml.entry_point
    if isinstance(api, APIFragment):
        metadata = (('title', api.title), ('version', api.version), ('baseUri', api.base_uri))
        found += (
            symbol(name, SymbolKind.METADATA, facet, facet.value)
            for name, facet in metadata
            if facet is not None and facet.location == uri
        )
        parameters = (
            _parameter(name, param) for name, param in api.base_uri_parameters.items() if param.base.location == uri
        )
        found.append(_group('baseUriParameters', parameters))
        items = (
            symbol(str(item.title.value), SymbolKind.DOCUMENTATION, item, key=item.title.value_pos)
            for item in authored.documentation(raml, uri)
            if item.title is not None
        )
        found.append(_group('documentation', items))
    found.append(
        _group('uses', (symbol(name, SymbolKind.LIBRARY, link, link.value) for name, link in fragment.uses.items()))
    )
    sections: dict[str, list[Symbol | None]] = {}
    for key, name, entity in authored.declarations(raml, uri):
        sections.setdefault(key, []).append(_declaration(name, DECLARATION_KINDS[key], entity))
    found += (_group(key, entries) for key, entries in sections.items())
    found += (_resource(written) for written in authored.resources(raml, uri))
    return _ordered(found)


def _declaration(name: str, kind: SymbolKind, entity: object) -> Symbol | None:
    if isinstance(entity, BaseShape):
        return _type(name, kind, entity)
    if isinstance(entity, SecuritySchemeDefinition):
        found = symbol(name, kind, entity, entity.resolved().type)
        described = entity.described_by
        if found is not None and described is not None:
            _adopt(found, [_group('describedBy', _message(described, entity))])
        return found
    return symbol(name, kind, cast('TemplateDefinition', entity))


def _type(name: str, kind: SymbolKind, base: BaseShape) -> Symbol | None:
    """A type-like symbol: `base` and the members it declares."""
    found = symbol(name, kind, base, _written(base))
    if found is None:
        return None
    found.form = 'enum' if base.enum is not None else base.type or ''
    _members(base, found)
    return found


def _members(base: BaseShape, parent: Symbol) -> None:
    """Add to `parent` the members `base` declares: not those it inherits,
    nor `items` an expression built (docs/16 § 10).
    """
    found: list[Symbol | None] = [
        _type(key if prop.required else f'{key}?', SymbolKind.PROPERTY, prop.base)
        for key, prop in authored.properties(base)
    ]
    found += (
        _type(f'/{key}/', SymbolKind.PROPERTY, pattern.base) for key, pattern in authored.pattern_properties(base)
    )
    items = authored.items(base)
    if items is not None:
        found.append(_type('items', SymbolKind.TYPE, items))
    if base.custom_facet_defs:
        facets = (
            _type(key if prop.required else f'{key}?', SymbolKind.FACET, prop.base)
            for key, prop in base.custom_facet_defs.items()
        )
        found.append(_group('facets', facets))
    _adopt(parent, found)


def _parameter(name: str, param: Parameter) -> Symbol | None:
    return _type(name if param.required else f'{name}?', SymbolKind.PARAMETER, param.declaration.base)


def _written(base: BaseShape) -> str:
    """The type `base` was declared with, as written: `common.Address`,
    `Book[] | Review`; a JSON schema by its kind; where nothing was, as hover
    names it.
    """
    if isinstance(base.shape, JsonShape):
        return 'JSON schema'
    written = base.type_expr
    if written is None or written.kind is NodeKind.MAPPING:
        return type_name(base)
    if written.kind is NodeKind.SEQUENCE:
        return ', '.join(item.value for item in written.content if item.kind is NodeKind.SCALAR)
    return written.value.lstrip()


def _resource(written: authored.WrittenResource) -> Symbol | None:
    """A resource as this file wrote it: at its key when the file declares
    it, or else a section, named by its path, holding what the file added.
    """
    endpoint = written.endpoint
    children: list[Symbol | None] = [_method(operation) for operation in written.operations]
    children += (_resource(child) for child in written.resources)
    if not written.here:
        return _group(endpoint.uri, children, SymbolKind.RESOURCE)
    found = symbol(endpoint.uri, SymbolKind.RESOURCE, endpoint, _shown(endpoint.display_name))
    if found is None:
        return None
    applied = [] if endpoint.resource_type is None else [endpoint.resource_type]
    parameters = (_parameter(name, param) for name, param in authored.parameters(endpoint, endpoint.uri_parameters))
    _adopt(
        found,
        [
            _applied('type', applied, endpoint),
            _applied('is', endpoint.traits, endpoint),
            _applied('securedBy', endpoint.secured_by if endpoint.explicit_secured_by else [], endpoint),
            _group('uriParameters', parameters),
            *children,
        ],
    )
    return found


def _method(operation: Operation) -> Symbol | None:
    found = symbol(operation.method, SymbolKind.METHOD, operation, _shown(operation.display_name))
    if found is None:
        return None
    children: list[Symbol | None] = [
        _applied('is', operation.traits, operation),
        _applied('securedBy', operation.secured_by if operation.explicit_secured_by else [], operation),
    ]
    if operation.request is not None:
        children += _message(operation.request, operation)
        children.append(_group('body', _bodies(operation.request.bodies, operation)))
    children += (
        _response(response)
        for response in operation.responses.values()
        if authored.wrote(operation, response.location, response.key_pos)
    )
    _adopt(found, children)
    return found


def _message(
    message: Request | SecuritySchemeDescription, owner: Operation | SecuritySchemeDefinition
) -> list[Symbol | None]:
    """A request's, or a `describedBy`'s, parameter groups and query string,
    as `owner` wrote them; a `describedBy`'s responses too.
    """
    query = (_parameter(name, param) for name, param in authored.parameters(owner, message.query_parameters))
    headers = (_parameter(name, param) for name, param in authored.parameters(owner, message.headers))
    found: list[Symbol | None] = [_group('queryParameters', query), _group('headers', headers)]
    string = message.query_string
    if string is not None and authored.wrote(owner, string.location, string.key_pos):
        found.append(_type('queryString', SymbolKind.TYPE, string))
    if isinstance(message, SecuritySchemeDescription):
        found += (
            _response(response)
            for response in message.responses.values()
            if authored.wrote(owner, response.location, response.key_pos)
        )
    return found


def _response(response: Response) -> Symbol | None:
    shown = _shown(response.display_name) or _shown(response.description)
    found = symbol(response.code, SymbolKind.RESPONSE, response, shown)
    if found is None:
        return None
    headers = (_parameter(name, param) for name, param in authored.parameters(response, response.headers))
    _adopt(found, [_group('headers', headers), _group('body', _bodies(response.bodies, response))])
    return found


def _bodies(bodies: Mapping[str, Body], owner: Operation | Response) -> Iterator[Symbol | None]:
    """One symbol per body `owner` wrote: a `body:` with no media type is one
    body per default media type (docs/08 § 6.3), named by all of them.
    """
    for same in authored.bodies(bodies):
        body, shape = same[0], same[0].shape
        if not authored.wrote(owner, body.location, body.key_pos):
            continue
        name = ', '.join(each.media_type for each in same)
        # The body's own key: its shape's is none for `body: Book`.
        found = symbol(name, SymbolKind.BODY, body, '' if shape is None else _written(shape))
        if found is not None and shape is not None:
            _members(shape, found)
        yield found


def _applied(name: str, refs: Sequence[DirectiveRef | SecurityScheme], owner: authored.Placed) -> Symbol | None:
    """What a resource or method applies, `is:` or `securedBy:`, as one entry
    naming each it wrote.
    """
    placed = [ref for ref in refs if authored.wrote(owner, ref.location, ref.key_pos)]
    if not placed:
        return None
    spans = [Position.covering((ref.key_pos, ref.value_pos)) for ref in placed]
    names = detail_line(', '.join(ref.name for ref in placed))
    return Symbol(
        name, SymbolKind.METADATA, placed[0].location, Position.covering(spans), placed[0].key_pos, detail=names
    )


def _group(name: str, children: Iterable[Symbol | None], kind: SymbolKind = SymbolKind.SECTION) -> Symbol | None:
    """A section holding `children`, in the order written, or `None` for an
    empty one. The model keeps no position for a section's key, so it spans
    its entries and selects the first.
    """
    placed = _ordered(children)
    if not placed:
        return None
    first = placed[0]
    span = Position.covering([child.span for child in placed])
    return Symbol(name, kind, first.uri, span, first.selection, placed)


def _adopt(parent: Symbol, found: Iterable[Symbol | None]) -> None:
    """Add `found` to `parent`, in the order written: those in its file only,
    since a member another file wrote, an `!include`d type's, is that file's.
    """
    parent.children += _ordered(each for each in found if each is None or each.uri == parent.uri)


def _ordered(found: Iterable[Symbol | None]) -> list[Symbol]:
    placed = [each for each in found if each is not None]
    if len(placed) > 1:
        placed.sort(key=lambda each: (each.span.line, each.span.column))
    return placed


def _shown(facet: ScalarFacet[str] | None) -> str:
    return '' if facet is None else str(facet.value)
