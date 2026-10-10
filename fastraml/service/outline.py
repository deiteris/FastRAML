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

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.parser.documentation import DocumentationItem
    from fastraml.parser.endpoints import Body, Operation, Request, Response
    from fastraml.parser.templates import TemplateDefinition
    from fastraml.registry import Identified, Raml
    from fastraml.service.workspace import Snapshot
    from fastraml.types.base import Parameter, ScalarFacet

__all__ = ['document_symbols']


class _Written:
    """The section keys entities wrote in one file (`Raml.written_sections`),
    by owner, indexed once per outline.
    """

    __slots__ = ('_by_owner',)

    def __init__(self, raml: Raml, uri: str) -> None:
        self._by_owner: dict[int, dict[str, _Placed]] = {}
        for each in raml.written_sections.get(uri, ()):
            self._by_owner.setdefault(each.owner, {}).setdefault(each.name, (uri, each.span, each.key))

    def of(self, owner: Identified) -> Mapping[str, _Placed]:
        return self._by_owner.get(owner.id, {})


#: Where a section key was written: its file, its span and the key's own span.
type _Placed = tuple[str, Position, Position]


def document_symbols(snapshot: Snapshot, uri: str) -> list[Symbol]:
    """Borrow the read-only outline cached for this URI and snapshot."""
    if uri not in snapshot.outlines:
        snapshot.outlines[uri] = _document_symbols(snapshot, uri)
    return snapshot.outlines[uri]


def _document_symbols(snapshot: Snapshot, uri: str) -> list[Symbol]:
    """The outline of `uri`: its metadata, its `uses:`, a section per
    declaration table, its documentation and its resources.

    Declarations only, as a code outline lists them: a type and its members,
    not its examples or annotations. A type's detail is its type as written,
    and an optional member is named `name?`. A section is placed at the key
    its owner wrote, which the parser records, and listed even when empty.

    What an Extension or an Overlay adds is outlined in it: a type it declared
    in the master's table, and under a master resource's path, a method it
    added there.
    """
    raml = snapshot.raml
    semantic = snapshot.semantic
    if raml is None or semantic is None or uri not in raml.fragments:
        return []
    written = _Written(raml, uri)
    root = written.of(raml.fragments[uri])
    found: list[Symbol | None] = [
        symbol(name, SymbolKind.METADATA, facet, facet.value) for name, facet in authored.metadata(raml, uri)
    ]
    parameters = (_parameter(name, param, written) for name, param in authored.base_uri_parameters(raml, uri))
    found.append(_group('baseUriParameters', parameters, root))
    found.append(_group('documentation', map(_documentation, authored.documentation(raml, uri)), root))
    uses = (symbol(name, SymbolKind.LIBRARY, link, link.value) for name, link in authored.uses(raml, uri).items())
    found.append(_group('uses', uses, root))
    sections: dict[str, list[Symbol | None]] = {key: [] for key in DECLARATION_KINDS if _spelled(key, root) in root}
    for key, name, entity in semantic.by_uri.get(uri, ()):
        sections.setdefault(key, []).append(_declaration(name, DECLARATION_KINDS[key], entity, written))
    found += (_group(_spelled(key, root), entries, root) for key, entries in sections.items())
    found += (_resource(each, written, root) for each in authored.resources(raml, uri))
    found += _fragment_body(authored.fragment_body(raml, uri), written)
    return _here(uri, found)


def _spelled(key: str, written: Mapping[str, _Placed]) -> str:
    """The table's key as the file wrote it: `schemas` is `types` in the model."""
    return 'schemas' if key == 'types' and 'schemas' in written and 'types' not in written else key


def _fragment_body(body: BaseShape | SecuritySchemeDefinition | None, written: _Written) -> list[Symbol | None]:
    """What a fragment file that is one declaration wrote, at the top: the
    file is the declaration, and its name is the file's.
    """
    if isinstance(body, BaseShape):
        return _member_symbols(body, written)
    return [] if body is None else [_described(body, written)]


def _documentation(item: DocumentationItem) -> Symbol | None:
    """An item has no key, and selects its title."""
    title = item.title
    return None if title is None else symbol(str(title.value), SymbolKind.DOCUMENTATION, item, key=title.value_pos)


def _declaration(name: str, kind: SymbolKind, entity: object, written: _Written) -> Symbol | None:
    if isinstance(entity, BaseShape):
        return _type(name, kind, entity, written)
    if isinstance(entity, SecuritySchemeDefinition):
        found = symbol(name, kind, entity, entity.resolved().type)
        if found is not None:
            _adopt(found, [_described(entity, written)])
        return found
    return symbol(name, kind, cast('TemplateDefinition', entity))


def _type(name: str, kind: SymbolKind, base: BaseShape, written: _Written) -> Symbol | None:
    """A type-like symbol: `base` and the members it declares."""
    found = symbol(name, kind, base, _written(base))
    if found is None:
        return None
    found.form = 'enum' if base.enum is not None else base.type or ''
    _adopt(found, _member_symbols(base, written))
    return found


def _described(definition: SecuritySchemeDefinition, written: _Written) -> Symbol | None:
    """A security scheme's `describedBy`, as the definition wrote it."""
    described = definition.described_by
    if described is None:
        return None
    children = _message(described, definition, written, written.of(described))
    return _group('describedBy', children, written.of(definition))


def _member_symbols(base: BaseShape, written: _Written) -> list[Symbol | None]:
    """The members `base` declares: not those it inherits, nor `items` an
    expression built (docs/16 § 10).
    """
    found: list[Symbol | None] = [
        _type(key if prop.required else f'{key}?', SymbolKind.PROPERTY, prop.base, written)
        for key, prop in authored.properties(base)
    ]
    found += (
        _type(f'/{key}/', SymbolKind.PROPERTY, pattern.base, written)
        for key, pattern in authored.pattern_properties(base)
    )
    items = authored.items(base)
    if items is not None:
        found.append(_type('items', SymbolKind.TYPE, items, written))
    facets = (
        _type(key if prop.required else f'{key}?', SymbolKind.FACET, prop.base, written)
        for key, prop in authored.facets(base)
    )
    found.append(_group('facets', facets, written.of(base)))
    return found


def _parameter(name: str, param: Parameter, written: _Written) -> Symbol | None:
    return _type(name if param.required else f'{name}?', SymbolKind.PARAMETER, param.declaration.base, written)


def _written(base: BaseShape) -> str:
    """The type `base` was declared with, as written: `common.Address`,
    `Book[] | Review`; a JSON schema by its kind; where nothing was, as hover
    names it.
    """
    if isinstance(base.shape, JsonShape):
        return 'JSON schema'
    written = base.type_expr
    if written is None:
        return type_name(base)
    return written.value.lstrip()


def _resource(resource: authored.WrittenResource, written: _Written, root: Mapping[str, _Placed]) -> Symbol | None:
    """A resource as this file wrote it: at its key when the file declares
    it, or else at the key it restated it under, holding what it added.
    """
    endpoint = resource.endpoint
    children: list[Symbol | None] = [_method(operation, written) for operation in resource.operations]
    children += (_resource(child, written, root) for child in resource.resources)
    if not resource.here:
        # An Overlay or Extension recorded the path it restated (docs/21 § 4).
        return _group(endpoint.uri, children, root, SymbolKind.RESOURCE, key=endpoint.full_uri)
    found = symbol(endpoint.uri, SymbolKind.RESOURCE, endpoint, _shown(endpoint.display_name))
    if found is None:
        return None
    applied = () if endpoint.resource_type is None else (endpoint.resource_type,)
    parameters = (
        _parameter(name, param, written) for name, param in authored.parameters(endpoint, endpoint.uri_parameters)
    )
    _adopt(
        found,
        [
            _applied('type', authored.members(endpoint, applied)),
            _applied('is', authored.members(endpoint, endpoint.traits)),
            _applied('securedBy', authored.secured_by(endpoint)),
            _group('uriParameters', parameters, written.of(endpoint)),
            *children,
        ],
    )
    return found


def _method(operation: Operation, written: _Written) -> Symbol | None:
    found = symbol(operation.method, SymbolKind.METHOD, operation, _shown(operation.display_name))
    if found is None:
        return None
    sections = written.of(operation)
    children: list[Symbol | None] = [
        _applied('is', authored.members(operation, operation.traits)),
        _applied('securedBy', authored.secured_by(operation)),
    ]
    if operation.request is not None:
        children += _message(operation.request, operation, written, sections)
        children.append(_group('body', _bodies(operation.request.bodies, operation, written), sections))
    children += (_response(response, written) for response in authored.members(operation, operation.responses.values()))
    _adopt(found, children)
    return found


def _message(
    message: Request | SecuritySchemeDescription,
    owner: Operation | SecuritySchemeDefinition,
    written: _Written,
    sections: Mapping[str, _Placed],
) -> list[Symbol | None]:
    """A request's, or a `describedBy`'s, parameter groups and query string,
    as `owner` wrote them, with the section keys it recorded; a `describedBy`'s
    responses too.
    """
    query = (_parameter(name, param, written) for name, param in authored.parameters(owner, message.query_parameters))
    headers = (_parameter(name, param, written) for name, param in authored.parameters(owner, message.headers))
    found: list[Symbol | None] = [_group('queryParameters', query, sections), _group('headers', headers, sections)]
    string = message.query_string
    if string is not None and authored.wrote(owner, string.location, string.key_pos):
        found.append(_type('queryString', SymbolKind.TYPE, string, written))
    if isinstance(message, SecuritySchemeDescription):
        found += (_response(response, written) for response in authored.members(owner, message.responses.values()))
    return found


def _response(response: Response, written: _Written) -> Symbol | None:
    shown = _shown(response.display_name) or _shown(response.description)
    found = symbol(response.code, SymbolKind.RESPONSE, response, shown)
    if found is None:
        return None
    sections = written.of(response)
    headers = (_parameter(name, param, written) for name, param in authored.parameters(response, response.headers))
    _adopt(
        found,
        [
            _group('headers', headers, sections),
            _group('body', _bodies(response.bodies, response, written), sections),
        ],
    )
    return found


def _bodies(bodies: Mapping[str, Body], owner: Operation | Response, written: _Written) -> Iterator[Symbol | None]:
    """One symbol per body `owner` wrote: a `body:` with no media type is one
    body per default media type (docs/08 § 6.3), named by all of them.
    """
    for same in authored.bodies(owner, bodies):
        body, shape = same[0], same[0].shape
        name = ', '.join(each.media_type for each in same)
        # The body's own key: its shape's is none for `body: Book`.
        found = symbol(name, SymbolKind.BODY, body, '' if shape is None else _written(shape))
        if found is not None and shape is not None:
            _adopt(found, _member_symbols(shape, written))
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


def _group(
    name: str,
    children: Iterable[Symbol | None],
    sections: Mapping[str, _Placed],
    kind: SymbolKind = SymbolKind.SECTION,
    *,
    key: str = '',
) -> Symbol | None:
    """A section holding `children`, in the order written, at the key its
    owner wrote, `sections[key or name]`. One the parser recorded no key for,
    such as a section a template supplied, spans its entries and selects the
    first, or is `None` when empty.
    """
    placed = _ordered(children)
    at = sections.get(key or name)
    if at is not None:
        uri, span, selection = at
        return Symbol(name, kind, uri, span, selection, placed)
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
