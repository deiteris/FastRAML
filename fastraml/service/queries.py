"""What an editor asks of a snapshot (docs/21 § 4).

Every query takes fastRAML positions, 1-based code points with exclusive ends
(docs/11 § 1), and answers in them; the adapter converts. Each reads what a
pass bound or a view derived, and none resolves a name itself: a name the
occurrence index does not hold has no answer here.

A query on a snapshot that stopped early answers from the stages it completed
(docs/13 § 1): an occurrence exists only where the pass that binds it ran.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from fastraml.errors import RamlError
from fastraml.facet_names import (
    FACET_ANNOTATION_TYPES,
    FACET_RESOURCE_TYPES,
    FACET_SECURITY_SCHEMES,
    FACET_TRAITS,
    FACET_TYPES,
)
from fastraml.parser.fragments import APIFragment, Library
from fastraml.parser.security import SecuritySchemeDefinition, SecuritySchemeDescription
from fastraml.positions import Position
from fastraml.types.base import BaseShape
from fastraml.types.complex_ import ArrayShape, ObjectShape
from fastraml.uris import relative_to
from fastraml.views.occurrences import Kind, Role
from fastraml.views.render import render, type_name
from fastraml.views.tree import build_tree
from fastraml.yamlnode import TAG_INCLUDE, Node, NodeKind, compose, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from fastraml.errors import Trace
    from fastraml.parser.directives import DirectiveRef, SecurityScheme
    from fastraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
    from fastraml.parser.templates import TemplateDefinition
    from fastraml.registry import Raml
    from fastraml.service.workspace import Snapshot
    from fastraml.types.base import Parameter, Property, ScalarFacet
    from fastraml.views.lint import Finding
    from fastraml.views.occurrences import Occurrence

__all__ = [
    'Diagnostic',
    'Related',
    'Site',
    'Symbol',
    'SymbolKind',
    'definition',
    'diagnostics',
    'document_symbols',
    'folding_ranges',
    'highlights',
    'hover',
    'links',
    'references',
    'selection_ranges',
    'subtypes',
    'supertypes',
    'suppression',
    'tree',
    'type_at',
    'workspace_symbols',
]

#: The source a parser diagnostic is reported under, and a lint finding.
SOURCE: Final = 'fastraml'
LINT_SOURCE: Final = 'fastraml-lint'


@dataclass(frozen=True, slots=True)
class Site:
    """A span in a file."""

    uri: str
    span: Position


@dataclass(frozen=True, slots=True)
class Related:
    """An outer frame of a diagnostic's chain, where it has a position."""

    site: Site
    message: str


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One problem at one span: a parser chain or a lint finding.

    `code` is the message key, or the lint rule's id; `info` is the frame's or
    the finding's variables (docs/11 § 6).
    """

    site: Site
    severity: str
    code: str
    message: str
    source: str
    info: Mapping[str, Any] = field(default_factory=dict)
    related: tuple[Related, ...] = ()


class SymbolKind(StrEnum):
    """What a symbol is: an occurrence kind, or one of the model's own."""

    TYPE = Kind.TYPE
    ANNOTATION_TYPE = Kind.ANNOTATION_TYPE
    TRAIT = Kind.TRAIT
    RESOURCE_TYPE = Kind.RESOURCE_TYPE
    SECURITY_SCHEME = Kind.SECURITY_SCHEME
    LIBRARY = Kind.LIBRARY
    PROPERTY = Kind.PROPERTY
    FACET = Kind.FACET
    RESOURCE = 'resource'
    METHOD = 'method'
    DOCUMENTATION = 'documentation'
    PARAMETER = 'parameter'
    RESPONSE = 'response'
    BODY = 'body'
    #: A scalar the file states, `title`, or what an entry applies, `is:`.
    METADATA = 'metadata'
    #: A group of entries, as the file groups them: `types:`, `headers:`.
    SECTION = 'section'


@dataclass(slots=True)
class Symbol:
    """A named construct: its whole span, the span of its name, what it holds,
    and a line of detail, such as the type it names.
    """

    name: str
    kind: SymbolKind
    uri: str
    span: Position
    selection: Position
    children: list[Symbol] = field(default_factory=list)
    detail: str = ''
    #: A type's kind, `object`, `array`, `union`, `enum` or a scalar's, for its icon.
    form: str = ''


# -- diagnostics --------------------------------------------------------------


def diagnostics(snapshot: Snapshot, *, lint: bool = True) -> dict[str, list[Diagnostic]]:
    """Every problem the snapshot holds, by the file it is in; a file with
    none has no entry.

    A chain is reported at its innermost frame with a position, with the outer
    frames as related information (docs/21 § 4.1). A chain with no position is
    reported at the start of its innermost frame's file.
    """
    found: dict[str, list[Diagnostic]] = {}
    if snapshot.error is not None:
        for chain in snapshot.error.chains():
            diagnostic = _from_chain(chain)
            found.setdefault(diagnostic.site.uri, []).append(diagnostic)
    if lint:
        for finding in snapshot.findings:
            diagnostic = _from_finding(finding)
            found.setdefault(diagnostic.site.uri, []).append(diagnostic)
    return found


def _from_chain(chain: Sequence[Trace]) -> Diagnostic:
    placed = [frame for frame in chain if frame.position is not None and frame.position.is_known]
    inner = placed[-1] if placed else chain[-1]
    span = inner.position if inner in placed and inner.position is not None else _START
    # The outer frames not at the reported site, then every frame's origin:
    # the constraint a value broke.
    related = tuple(
        Related(Site(frame.location, frame.position), frame.rendered_message())
        for frame in placed
        if frame is not inner
        and frame.position is not None
        and (frame.location, frame.position) != (inner.location, span)
    ) + tuple(
        Related(Site(frame.origin.location, frame.origin.position), frame.origin.rendered_message())
        for frame in chain
        if frame.origin is not None and frame.origin.position is not None and frame.origin.position.is_known
    )
    innermost = chain[-1]
    return Diagnostic(
        site=Site(inner.location, span),
        severity='error',
        code=innermost.message,
        message=innermost.rendered_message(),
        source=SOURCE,
        info=innermost.info,
        related=related,
    )


def _from_finding(finding: Finding) -> Diagnostic:
    span = finding.position if finding.position.is_known else _START
    return Diagnostic(
        site=Site(finding.location, span),
        severity=str(finding.severity),
        code=finding.rule,
        message=finding.rendered_message(),
        source=LINT_SOURCE,
        info=finding.info,
    )


_START: Final = Position(1, 1, 1, 1)

#: The lexical suppression directive (docs/18 § 4).
_DIRECTIVE: Final = '# fastraml: ignore '


def suppression(line: str, rule: str) -> str:
    """The line to insert before `line`, the text of a finding's line, to
    suppress the lint rule `rule` there. Only a lint finding can be
    suppressed: its source is `LINT_SOURCE`.

    The directive takes the line's indentation, so it stays in the block it
    annotates.
    """
    return f'{line[: len(line) - len(line.lstrip())]}{_DIRECTIVE}{rule}\n'


# -- views ----------------------------------------------------------------------


def tree(snapshot: Snapshot) -> str | None:
    """The effective document as `fastraml tree` prints it (docs/16 § 6), or
    `None` when the parse stopped before unwrap.

    JSON text rather than a value, so an integer larger than a double reaches
    a JavaScript reader as written.
    """
    raml = snapshot.raml
    return None if raml is None or not raml.unwrapped else json.dumps(build_tree(raml))


# -- names ----------------------------------------------------------------------


def _at(snapshot: Snapshot, uri: str, line: int, column: int) -> list[Occurrence]:
    occurrences = snapshot.occurrences
    return [] if occurrences is None else occurrences.at(uri, line, column)


def _site(occurrence: Occurrence) -> Site:
    return Site(occurrence.uri, occurrence.span)


def definition(snapshot: Snapshot, uri: str, line: int, column: int) -> list[Site]:
    """Where the name under the cursor is defined; a file for a path."""
    occurrences = snapshot.occurrences
    raml = snapshot.raml
    if occurrences is None or raml is None:
        return []
    found: list[Site] = []
    for occurrence in occurrences.at(uri, line, column):
        if occurrence.target is None:
            continue
        if occurrence.role is Role.LINK:
            found += [
                Site(location, _START)
                for location, fragment in raml.fragments.items()
                if fragment.id == occurrence.target
            ]
            continue
        found += [_site(other) for other in occurrences.of(occurrence.target) if other.role is Role.DEFINITION]
    return found


def references(snapshot: Snapshot, uri: str, line: int, column: int, *, declaration: bool = True) -> list[Site]:
    """Every place the entity under the cursor is named, in source order."""
    occurrences = snapshot.occurrences
    if occurrences is None:
        return []
    found: list[Site] = []
    for target in _targets(snapshot, uri, line, column):
        found += [
            _site(other)
            for other in occurrences.of(target)
            if other.role is not Role.LINK and (declaration or other.role is not Role.DEFINITION)
        ]
    return sorted(found, key=lambda site: (site.uri, site.span.line, site.span.column))


def highlights(snapshot: Snapshot, uri: str, line: int, column: int) -> list[tuple[Position, bool]]:
    """The spans in this file naming the entity under the cursor, each with
    whether it is the definition.
    """
    occurrences = snapshot.occurrences
    if occurrences is None:
        return []
    return [
        (other.span, other.role is Role.DEFINITION)
        for target in _targets(snapshot, uri, line, column)
        for other in occurrences.of(target)
        if other.uri == uri and other.role is not Role.LINK
    ]


def _targets(snapshot: Snapshot, uri: str, line: int, column: int) -> list[int]:
    return [
        found.target
        for found in _at(snapshot, uri, line, column)
        if found.target is not None and found.role is not Role.LINK
    ]


# -- hover ------------------------------------------------------------------------


def hover(snapshot: Snapshot, uri: str, line: int, column: int) -> tuple[str, Position] | None:
    """Markdown for the name under the cursor, and the span it covers."""
    raml = snapshot.raml
    found = _at(snapshot, uri, line, column)
    if raml is None or not found:
        return None
    occurrence = found[0]
    text = _describe(snapshot, raml, occurrence)
    return None if text is None else (text, occurrence.span)


def _describe(snapshot: Snapshot, raml: Raml, occurrence: Occurrence) -> str | None:
    if occurrence.role is Role.BUILTIN:
        return f'built-in type `{occurrence.written}`'
    target = occurrence.target
    if target is None:
        return None
    # Paths in the text are relative to the root's directory.
    root = snapshot.root.rpartition('/')[0] + '/'
    if occurrence.role is Role.LINK:
        return next(
            (
                f'`{relative_to(location, root)}`'
                for location, fragment in raml.fragments.items()
                if fragment.id == target
            ),
            None,
        )
    entity = _entity(raml, target)
    if isinstance(entity, BaseShape):
        return '```yaml\n' + '\n'.join(render(entity, root=root)) + '\n```'
    if entity is None:
        return None
    kind = occurrence.kind.replace('_', ' ')
    lines = [f'**{kind}** `{getattr(entity, "name", occurrence.written)}`']
    if occurrence.kind is Kind.LIBRARY:
        lines.append(f'`{entity.value}`')
    scheme_type = getattr(entity, 'type', '')
    if scheme_type:
        lines.append(f'type: {scheme_type}')
    variables = getattr(entity, 'declared_variables', None)
    if variables:
        lines.append('parameters: ' + ', '.join(f'`{name}`' for name in sorted(variables)))
    for facet in ('usage', 'description'):
        value = getattr(entity, facet, None)
        if value is not None:
            lines.append(str(value.value))
    return '\n\n'.join(lines)


def _entity(raml: Raml, target: int) -> Any:
    """The declaration, `uses:` entry or shape with the id `target`."""
    for fragment in raml.fragments.values():
        for link in fragment.uses.values():
            if link.id == target:
                return link
        if isinstance(fragment, (Library, APIFragment)):
            for _key, _name, entity in fragment.declarations():
                if entity.id == target:
                    return entity
    return next((base for base in raml.shapes if base.id == target), None)


# -- symbols ------------------------------------------------------------------------

#: The symbol kind of each declaration table, by the key it is written under.
_KINDS: Final = {
    FACET_TYPES: SymbolKind.TYPE,
    FACET_ANNOTATION_TYPES: SymbolKind.ANNOTATION_TYPE,
    FACET_TRAITS: SymbolKind.TRAIT,
    FACET_RESOURCE_TYPES: SymbolKind.RESOURCE_TYPE,
    FACET_SECURITY_SCHEMES: SymbolKind.SECURITY_SCHEME,
}


def document_symbols(snapshot: Snapshot, uri: str) -> list[Symbol]:
    """The file's outline, from the model, grouped as the file is: its
    metadata, each declaration section, and its resources.

    Declarations only, as a code outline lists them: a type and its
    properties, not its examples or annotations. A type's detail is its type
    as written, and an optional member is named `name?`. A section the model
    records no key for, `types:` or a method's `headers:`, spans its entries.

    An entry is listed only inside the entry that wrote it, in the same file.
    An inherited property is outlined under the type that declared it; a
    method a resource type contributed, under nothing, since a trait or
    resource type is listed by name alone: its body is decoded only where it
    is applied (docs/08 § 5).
    """
    raml = snapshot.raml
    fragment = None if raml is None else raml.fragments.get(uri)
    if raml is None or not isinstance(fragment, (Library, APIFragment)):
        return []
    found: list[Symbol | None] = []
    if isinstance(fragment, APIFragment):
        metadata = (('title', fragment.title), ('version', fragment.version), ('baseUri', fragment.base_uri))
        found += (_symbol(name, SymbolKind.METADATA, facet, facet.value) for name, facet in metadata if facet)
        parameters = (_parameter(name, param, None) for name, param in fragment.base_uri_parameters.items())
        found.append(_group('baseUriParameters', _placed(parameters, uri)))
        items = (
            _symbol(str(item.title.value), SymbolKind.DOCUMENTATION, item, key=item.title.value_pos)
            for item in fragment.documentation
            if item.title is not None
        )
        found.append(_group('documentation', _placed(items, uri)))
    links = (_symbol(name, SymbolKind.LIBRARY, link, link.value) for name, link in fragment.uses.items())
    found.append(_group('uses', _placed(links, uri)))
    sections: dict[str, list[Symbol | None]] = {}
    for key, name, entity in fragment.declarations():
        sections.setdefault(key, []).append(_declaration(name, _KINDS[key], entity))
    found += (_group(key, _placed(entries, uri)) for key, entries in sections.items())
    if isinstance(fragment, APIFragment):
        found += (
            _resource(endpoint, None) for endpoint in raml.endpoints.values() if endpoint.full_uri == endpoint.uri
        )
    return _placed(found, uri)


def _declaration(name: str, kind: SymbolKind, entity: object) -> Symbol | None:
    if isinstance(entity, BaseShape):
        return _type(name, kind, entity, None)
    if isinstance(entity, SecuritySchemeDefinition):
        symbol = _symbol(name, kind, entity, entity.resolved().type)
        described = entity.described_by
        if symbol is not None and described is not None:
            _adopt(symbol, [_group('describedBy', _placed(_message(described, symbol), symbol))])
        return symbol
    template = cast('TemplateDefinition', entity)
    return _symbol(name, kind, template)


def _type(name: str, kind: SymbolKind, base: BaseShape, parent: Symbol | None) -> Symbol | None:
    """A type-like symbol: `base` and the members it declares, or `None` where
    it is not written inside `parent`, which is checked before descending,
    since an inherited property is the parent type's own.
    """
    symbol = _symbol(name, kind, base, _written(base))
    if symbol is None or not _inside(symbol, parent):
        return None
    symbol.form = 'enum' if base.enum is not None else base.type or ''
    _members(base, symbol)
    return symbol


def _members(base: BaseShape, symbol: Symbol) -> None:
    """Add to `symbol` the members `base` declares inside it."""
    shape = base.shape
    found: list[Symbol | None] = []
    # An alias shares its referent's containers (docs/07 § 3): they are the referent's.
    if base.alias is None and isinstance(shape, ObjectShape):
        found += (_property(key, prop, symbol) for key, prop in (shape.properties or {}).items())
        found += (
            _type(f'/{key}/', SymbolKind.PROPERTY, pattern.base, symbol)
            for key, pattern in (shape.pattern_properties or {}).items()
        )
    # Items a type expression built, `Book[]`, are placed at the array's own key.
    items = shape.items if base.alias is None and isinstance(shape, ArrayShape) else None
    if items is not None and items.key_pos != base.key_pos:
        found.append(_type('items', SymbolKind.TYPE, items, symbol))
    if base.custom_facet_defs:
        facets = (_property(key, prop, symbol, SymbolKind.FACET) for key, prop in base.custom_facet_defs.items())
        found.append(_group('facets', _placed(facets, symbol)))
    if found:
        _adopt(symbol, found)


def _property(key: str, prop: Property, parent: Symbol, kind: SymbolKind = SymbolKind.PROPERTY) -> Symbol | None:
    return _type(key if prop.required else f'{key}?', kind, prop.base, parent)


def _parameter(name: str, param: Parameter, parent: Symbol | None) -> Symbol | None:
    return _type(name if param.required else f'{name}?', SymbolKind.PARAMETER, param.declaration.base, parent)


def _written(base: BaseShape) -> str:
    """The type `base` was declared with, as written: `common.Address`,
    `Book[] | Review`; where nothing was, as hover names it.
    """
    written = base.type_expr
    if written is None or written.kind is NodeKind.MAPPING:
        return type_name(base)
    if written.kind is NodeKind.SEQUENCE:
        return ', '.join(item.value for item in written.content if item.kind is NodeKind.SCALAR)
    text = written.value.lstrip()
    if written.tag != TAG_INCLUDE and text.startswith('{'):
        return 'JSON schema'
    if written.tag != TAG_INCLUDE and text.startswith('<'):
        return 'XML schema'
    return text


def _resource(endpoint: EndPoint, parent: Symbol | None) -> Symbol | None:
    symbol = _symbol(endpoint.uri, SymbolKind.RESOURCE, endpoint, _shown(endpoint.display_name))
    if symbol is None or not _inside(symbol, parent):
        return None
    applied = [] if endpoint.resource_type is None else [endpoint.resource_type]
    parameters = (_parameter(name, param, symbol) for name, param in endpoint.uri_parameters.items())
    found: list[Symbol | None] = [
        _applied('type', applied, symbol),
        _applied('is', endpoint.traits, symbol),
        _applied('securedBy', endpoint.secured_by if endpoint.explicit_secured_by else [], symbol),
        _group('uriParameters', _placed(parameters, symbol)),
    ]
    found += (_method(operation, symbol) for operation in endpoint.operations.values())
    found += (_resource(child, symbol) for child in endpoint.endpoints.values())
    _adopt(symbol, found)
    return symbol


def _method(operation: Operation, parent: Symbol) -> Symbol | None:
    symbol = _symbol(operation.method, SymbolKind.METHOD, operation, _shown(operation.display_name))
    if symbol is None or not _inside(symbol, parent):
        return None
    found: list[Symbol | None] = [
        _applied('is', operation.traits, symbol),
        _applied('securedBy', operation.secured_by if operation.explicit_secured_by else [], symbol),
    ]
    if operation.request is not None:
        found += _message(operation.request, symbol)
        found.append(_group('body', _placed(_bodies(operation.request.bodies, symbol), symbol)))
    found += (_response(response, symbol) for response in operation.responses.values())
    _adopt(symbol, found)
    return symbol


def _message(message: Request | SecuritySchemeDescription, parent: Symbol) -> list[Symbol | None]:
    """A request's, or a `describedBy`'s, parameter groups and query string;
    a `describedBy`'s responses too.
    """
    query = (_parameter(name, param, parent) for name, param in message.query_parameters.items())
    headers = (_parameter(name, param, parent) for name, param in message.headers.items())
    found: list[Symbol | None] = [
        _group('queryParameters', _placed(query, parent)),
        _group('headers', _placed(headers, parent)),
    ]
    if message.query_string is not None:
        found.append(_type('queryString', SymbolKind.TYPE, message.query_string, parent))
    if isinstance(message, SecuritySchemeDescription):
        found += (_response(response, parent) for response in message.responses.values())
    return found


def _response(response: Response, parent: Symbol) -> Symbol | None:
    shown = _shown(response.display_name) or _shown(response.description)
    symbol = _symbol(response.code, SymbolKind.RESPONSE, response, shown)
    if symbol is None or not _inside(symbol, parent):
        return None
    headers = (_parameter(name, param, symbol) for name, param in response.headers.items())
    bodies = _bodies(response.bodies, symbol)
    _adopt(symbol, [_group('headers', _placed(headers, symbol)), _group('body', _placed(bodies, symbol))])
    return symbol


def _bodies(bodies: Mapping[str, Body], parent: Symbol) -> Iterator[Symbol | None]:
    """One symbol per body written: a `body:` with no media type is one body
    per default media type (docs/08 § 6.3), named by all of them.
    """
    written: dict[Position, list[Body]] = {}
    for body in bodies.values():
        written.setdefault(body.key_pos, []).append(body)
    for same in written.values():
        body, shape = same[0], same[0].shape
        name = ', '.join(each.media_type for each in same)
        # The body's own key: its shape's is none for `body: Book`.
        symbol = _symbol(name, SymbolKind.BODY, body, '' if shape is None else _written(shape))
        if symbol is not None and shape is not None and _inside(symbol, parent):
            _members(shape, symbol)
        yield symbol


def _applied(name: str, refs: Sequence[DirectiveRef | SecurityScheme], parent: Symbol) -> Symbol | None:
    """What a resource or method applies, `is:` or `securedBy:`, as one entry
    naming each, spanning those written inside `parent`.
    """
    placed = [ref for ref in refs if ref.location == parent.uri and ref.value_pos.is_known]
    spans = [_spanning(ref.key_pos, ref.value_pos) for ref in placed]
    if not spans or not all(parent.span.contains(span) for span in spans):
        return None
    detail = _line(', '.join(ref.name for ref in placed))
    return Symbol(name, SymbolKind.METADATA, parent.uri, Position.covering(spans), placed[0].key_pos, detail=detail)


def _group(name: str, children: list[Symbol]) -> Symbol | None:
    """A section holding `children`, or `None` for an empty one. The model
    keeps no position for a section's key, so it spans its entries and
    selects the first.
    """
    if not children:
        return None
    first = children[0]
    span = Position.covering([child.span for child in children])
    return Symbol(name, SymbolKind.SECTION, first.uri, span, first.selection, children)


def _shown(facet: ScalarFacet[str] | None) -> str:
    return '' if facet is None else str(facet.value)


def _placed(found: Iterable[Symbol | None], within: Symbol | str) -> list[Symbol]:
    """What is written in the file `within`, or inside the entry `within`, in
    the order written.
    """
    if isinstance(within, str):
        placed = [symbol for symbol in found if symbol is not None and symbol.uri == within]
    else:
        placed = [symbol for symbol in found if symbol is not None and _inside(symbol, within)]
    if len(placed) > 1:
        placed.sort(key=lambda symbol: (symbol.span.line, symbol.span.column))
    return placed


def _adopt(parent: Symbol, found: Iterable[Symbol | None]) -> None:
    """Add what is written inside `parent`, in the order written."""
    parent.children += _placed(found, parent)


def _inside(symbol: Symbol, parent: Symbol | None) -> bool:
    return parent is None or (symbol.uri == parent.uri and parent.span.contains(symbol.span))


def _line(text: str) -> str:
    """The first line of `text`, cut to `_DETAIL_LIMIT` characters."""
    line = text.strip().partition('\n')[0]
    return line if len(line) <= _DETAIL_LIMIT else f'{line[: _DETAIL_LIMIT - 1]}…'


_DETAIL_LIMIT: Final = 80


def _declarations(fragment: object, wanted: str = '') -> Iterator[Symbol]:
    """The symbols of a fragment's declaration tables whose names hold
    `wanted`, casefolded, if one is given.
    """
    if not isinstance(fragment, (Library, APIFragment)):
        return
    for key, name, entity in fragment.declarations():
        if wanted in name.casefold() and (symbol := _symbol(name, _KINDS[key], entity)) is not None:
            yield symbol


class _Placed(Protocol):
    """An entity the model placed: the file and the key and value it was written at."""

    @property
    def location(self) -> str: ...
    @property
    def key_pos(self) -> Position | None: ...
    @property
    def value_pos(self) -> Position | None: ...


def _symbol(
    name: str, kind: SymbolKind, placed: _Placed, detail: str = '', *, key: Position | None = None
) -> Symbol | None:
    """A symbol selecting `placed`'s key, or `key`, and spanning key and
    value; `None` where the key has no position.

    The span holds the key, which a client requires of the selection: a
    documentation item has no key and selects its title, inside its value.
    """
    key = placed.key_pos if key is None else key
    if key is None or not key.is_known:
        return None
    return Symbol(name, kind, placed.location, _spanning(key, placed.value_pos), key, detail=_line(detail))


def _spanning(key: Position, value: Position | None) -> Position:
    """The span from `key` through `value`, holding both."""
    return key if value is None or not value.is_known else Position.covering((key, value))


def workspace_symbols(snapshots: Iterable[Snapshot], query: str) -> list[Symbol]:
    """Every declaration whose name holds `query`, case-insensitively, once
    each across the snapshots.
    """
    wanted = query.casefold()
    seen: set[tuple[str, int, int]] = set()
    found: list[Symbol] = []
    for snapshot in snapshots:
        raml = snapshot.raml
        if raml is None:
            continue
        for fragment in raml.fragments.values():
            for symbol in _declarations(fragment, wanted):
                key = (symbol.uri, symbol.selection.line, symbol.selection.column)
                if key not in seen:
                    seen.add(key)
                    found.append(symbol)
    return found


# -- links, folding and selection -------------------------------------------------------


def links(snapshot: Snapshot, uri: str) -> list[Site]:
    """Each path to a file written in `uri`, as `Site(target, span)`.

    The target is the file the path resolves to, found or not.
    """
    raml, occurrences = snapshot.raml, snapshot.occurrences
    if raml is None or occurrences is None:
        return []
    # Each path written in the file, as the occurrence index placed it.
    written: dict[str, list[Position]] = {}
    for occurrence in occurrences.in_file(uri):
        if occurrence.role is Role.LINK:
            written.setdefault(occurrence.written, []).append(occurrence.span)

    def span(at: Position, path: str) -> Position | None:
        if not at.is_known:
            return None
        return next((found for found in written.get(path, ()) if at.line <= found.line <= at.end_line), None)

    # A template applied twice records its includes twice.
    sites = dict.fromkeys(
        Site(ref.abs_uri, place)
        for ref in raml.include_refs.get(uri, ())
        if (place := span(ref.position, ref.path)) is not None
    )
    fragment = raml.fragments.get(uri)
    for link in () if fragment is None else fragment.uses.values():
        if link.link is not None and (place := span(link.value_pos, link.value)) is not None:
            sites[Site(link.link.location, place)] = None
    return list(sites)


def folding_ranges(text: str, uri: str) -> list[tuple[int, int]]:
    """Each mapping and sequence value spanning lines, from its key's line to
    its last leaf's, 1-based, from the text alone.
    """
    root = _compose(text, uri)
    if root is None:
        return []
    found: set[tuple[int, int]] = set()
    for key, value in _pairs(root):
        last = value.full_position.end_line
        if value.kind is not NodeKind.SCALAR and last > key.line:
            found.add((key.line, last))
    return sorted(found)


def selection_ranges(text: str, uri: str, line: int, column: int) -> list[Position]:
    """The spans holding `line:column`, innermost first: a token, its pair,
    the pair's mapping, and outward.
    """
    root = _compose(text, uri)
    if root is None:
        return []
    found: list[Position] = []
    node: Node | None = root
    while node is not None:
        found.append(node.full_position)
        node = _child_at(node, line, column, found)
    return [span for span in reversed(found) if span.is_known]


def _child_at(node: Node, line: int, column: int, found: list[Position]) -> Node | None:
    """The child of `node` holding `line:column`; a pair's span is added on the way."""
    if node.kind is NodeKind.MAPPING:
        for key, value in pairs(node):
            end = value.full_position
            pair = Position(key.line, key.column, end.end_line, end.end_column)
            if pair.holds(line, column):
                found.append(pair)
                return key if key.full_position.holds(line, column) else value
    elif node.kind is NodeKind.SEQUENCE:
        return next((item for item in node.content if item.full_position.holds(line, column)), None)
    return None


def _compose(text: str, uri: str) -> Node | None:
    try:
        return compose(text, uri=uri)
    except RamlError:
        return None


def _pairs(root: Node) -> Iterator[tuple[Node, Node]]:
    """Every key and value, and every sequence item as its own key, depth first."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.kind is NodeKind.MAPPING:
            for key, value in pairs(node):
                yield key, value
                stack.append(value)
        elif node.kind is NodeKind.SEQUENCE:
            for item in node.content:
                yield item, item
                stack.append(item)


# -- type hierarchy -------------------------------------------------------------------


def type_at(snapshot: Snapshot, uri: str, line: int, column: int) -> Symbol | None:
    """The declared type the name under the cursor stands for."""
    raml = snapshot.raml
    if raml is None:
        return None
    for occurrence in _at(snapshot, uri, line, column):
        if occurrence.kind in {Kind.TYPE, Kind.ANNOTATION_TYPE} and occurrence.target is not None:
            entity = _entity(raml, occurrence.target)
            if isinstance(entity, BaseShape):
                return _type_symbol(entity)
    return None


def supertypes(snapshot: Snapshot, item: Symbol) -> list[Symbol]:
    """The named types `item` names in its `type:`, an alias's referent among them."""
    base = _declared(snapshot, item)
    if base is None:
        return []
    return [symbol for parent in _parents(base) if (symbol := _type_symbol(parent)) is not None]


def subtypes(snapshot: Snapshot, item: Symbol) -> list[Symbol]:
    """The declared types that name `item` in their `type:`."""
    base = _declared(snapshot, item)
    raml = snapshot.raml
    if base is None or raml is None:
        return []
    return [
        symbol
        for fragment in raml.fragments.values()
        if isinstance(fragment, (Library, APIFragment))
        for _key, _name, child in fragment.declarations()
        if isinstance(child, BaseShape)
        and any(parent.id == base.id for parent in _parents(child))
        and (symbol := _type_symbol(child)) is not None
    ]


def _parents(base: BaseShape) -> Iterator[BaseShape]:
    """The named types `base` names in its `type:`, an alias's referent among them."""
    yield from base.inherits
    if base.alias is not None:
        yield base.alias


def _declared(snapshot: Snapshot, item: Symbol) -> BaseShape | None:
    """The type `item` names, found again by where its name is written: an
    item may come from an earlier snapshot (docs/21 § 4).
    """
    raml = snapshot.raml
    if raml is None:
        return None
    for occurrence in _at(snapshot, item.uri, item.selection.line, item.selection.column):
        if occurrence.role is Role.DEFINITION and occurrence.target is not None:
            entity = _entity(raml, occurrence.target)
            if isinstance(entity, BaseShape):
                return entity
    return None


def _type_symbol(base: BaseShape) -> Symbol | None:
    kind = SymbolKind.ANNOTATION_TYPE if base.is_annotation_type else SymbolKind.TYPE
    return None if not base.name else _symbol(base.name, kind, base)
