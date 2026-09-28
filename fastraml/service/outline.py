"""The outline: what a file wrote, grouped as the file groups it (docs/21 § 4).

Every entry is read from the model, through the authorship view
(`views/authored.py`), which says what a file or an entity wrote. This module
only shapes it into symbols: a section per table, a detail per entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastraml.parser.security import SecuritySchemeDefinition, SecuritySchemeDescription
from fastraml.positions import Position
from fastraml.service.queries import DECLARATION_KINDS, Symbol, SymbolKind, detail_line, symbol
from fastraml.types.base import BaseShape
from fastraml.types.jsonschema_ import JsonShape
from fastraml.views import authored
from fastraml.views.render import type_name
from fastraml.yamlnode import NodeKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.parser.documentation import DocumentationItem
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
    if raml is None or uri not in raml.fragments:
        return []
    found: list[Symbol | None] = [
        symbol(name, SymbolKind.METADATA, facet, facet.value) for name, facet in authored.metadata(raml, uri)
    ]
    parameters = (_parameter(name, param) for name, param in authored.base_uri_parameters(raml, uri))
    found.append(_group('baseUriParameters', parameters))
    found.append(_group('documentation', map(_documentation, authored.documentation(raml, uri))))
    uses = (symbol(name, SymbolKind.LIBRARY, link, link.value) for name, link in authored.uses(raml, uri).items())
    found.append(_group('uses', uses))
    sections: dict[str, list[Symbol | None]] = {}
    for key, name, entity in authored.declarations(raml, uri):
        sections.setdefault(key, []).append(_declaration(name, DECLARATION_KINDS[key], entity))
    found += (_group(key, entries) for key, entries in sections.items())
    found += (_resource(written) for written in authored.resources(raml, uri))
    found += _fragment_body(authored.fragment_body(raml, uri))
    return _here(uri, found)


def _fragment_body(body: BaseShape | SecuritySchemeDefinition | None) -> list[Symbol | None]:
    """What a fragment file that is one declaration wrote, at the top: the
    file is the declaration, and its name is the file's.
    """
    if isinstance(body, BaseShape):
        return _member_symbols(body)
    return [] if body is None else [_described(body)]


def _documentation(item: DocumentationItem) -> Symbol | None:
    """An item has no key, and selects its title."""
    title = item.title
    return None if title is None else symbol(str(title.value), SymbolKind.DOCUMENTATION, item, key=title.value_pos)


def _declaration(name: str, kind: SymbolKind, entity: object) -> Symbol | None:
    if isinstance(entity, BaseShape):
        return _type(name, kind, entity)
    if isinstance(entity, SecuritySchemeDefinition):
        found = symbol(name, kind, entity, entity.resolved().type)
        if found is not None:
            _adopt(found, [_described(entity)])
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


def _described(definition: SecuritySchemeDefinition) -> Symbol | None:
    """A security scheme's `describedBy`, as the definition wrote it."""
    described = definition.described_by
    return None if described is None else _group('describedBy', _message(described, definition))


def _members(base: BaseShape, parent: Symbol) -> None:
    """Add to `parent` the members `base` declares."""
    _adopt(parent, _member_symbols(base))


def _member_symbols(base: BaseShape) -> list[Symbol | None]:
    """The members `base` declares: not those it inherits, nor `items` an
    expression built (docs/16 § 10).
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
    facets = (
        _type(key if prop.required else f'{key}?', SymbolKind.FACET, prop.base) for key, prop in authored.facets(base)
    )
    found.append(_group('facets', facets))
    return found


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
    applied = () if endpoint.resource_type is None else (endpoint.resource_type,)
    parameters = (_parameter(name, param) for name, param in authored.parameters(endpoint, endpoint.uri_parameters))
    _adopt(
        found,
        [
            _applied('type', authored.members(endpoint, applied)),
            _applied('is', authored.members(endpoint, endpoint.traits)),
            _applied('securedBy', authored.secured_by(endpoint)),
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
        _applied('is', authored.members(operation, operation.traits)),
        _applied('securedBy', authored.secured_by(operation)),
    ]
    if operation.request is not None:
        children += _message(operation.request, operation)
        children.append(_group('body', _bodies(operation.request.bodies, operation)))
    children += (_response(response) for response in authored.members(operation, operation.responses.values()))
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
        found += (_response(response) for response in authored.members(owner, message.responses.values()))
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
    for same in authored.bodies(owner, bodies):
        body, shape = same[0], same[0].shape
        name = ', '.join(each.media_type for each in same)
        # The body's own key: its shape's is none for `body: Book`.
        found = symbol(name, SymbolKind.BODY, body, '' if shape is None else _written(shape))
        if found is not None and shape is not None:
            _members(shape, found)
        yield found


def _applied(name: str, refs: Iterable[DirectiveRef | SecurityScheme]) -> Symbol | None:
    """What a resource or method applies, `is:` or `securedBy:`, as one entry
    naming each of `refs`, those it wrote.
    """
    placed = list(refs)
    if not placed:
        return None
    spans = [ref.key_pos.spanning(ref.value_pos) for ref in placed]
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
    """Add `found` to `parent`, as `_here` keeps them."""
    parent.children += _here(parent.uri, found)


def _here(uri: str, found: Iterable[Symbol | None]) -> list[Symbol]:
    """`found`, in the order written: those in `uri` only, since a member
    another file wrote, an `!include`d type's, is that file's.
    """
    return _ordered(each for each in found if each is None or each.uri == uri)


def _ordered(found: Iterable[Symbol | None]) -> list[Symbol]:
    placed = [each for each in found if each is not None]
    if len(placed) > 1:
        placed.sort(key=lambda each: (each.span.line, each.span.column))
    return placed


def _shown(facet: ScalarFacet[str] | None) -> str:
    return '' if facet is None else str(facet.value)
