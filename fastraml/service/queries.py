"""What an editor asks of a snapshot (docs/21 § 4).

Every query takes fastRAML positions, 1-based code points with exclusive ends
(docs/11 § 1), and answers in them; the adapter converts. Each reads what a
pass bound or a view derived, and none resolves a name itself: a name the
occurrence index does not hold has no answer here.

A query on a snapshot that stopped early answers from the stages it completed
(docs/13 § 1): an occurrence exists only where the pass that binds it ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from fastraml.errors import RamlError
from fastraml.parser.fragments import APIFragment, Library
from fastraml.positions import Position
from fastraml.types.base import BaseShape
from fastraml.uris import relative_to
from fastraml.views.occurrences import Kind, Role
from fastraml.views.render import render
from fastraml.yamlnode import Node, NodeKind, compose, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from fastraml.errors import Trace
    from fastraml.parser.endpoints import EndPoint
    from fastraml.registry import Raml
    from fastraml.service.workspace import Snapshot
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


@dataclass(slots=True)
class Symbol:
    """A named construct: its whole span, the span of its name, and what it holds."""

    name: str
    kind: SymbolKind
    uri: str
    span: Position
    selection: Position
    children: list[Symbol] = field(default_factory=list)


# -- diagnostics --------------------------------------------------------------


def diagnostics(snapshot: Snapshot, *, lint: bool = True) -> dict[str, list[Diagnostic]]:
    """Every problem the snapshot holds, by the file it is in.

    A chain is reported at its innermost frame with a position, with the outer
    frames as related information (docs/21 § 4.1). A chain with no position is
    reported at the start of its innermost frame's file.
    """
    found: dict[str, list[Diagnostic]] = {uri: [] for uri in snapshot.read}
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
    related = tuple(
        Related(Site(frame.location, frame.position), frame.rendered_message())
        for frame in placed
        if frame is not inner and frame.position is not None
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


def suppression(text: str, line: int, rule: str) -> str:
    """The line to insert before the 1-based `line` of `text` to suppress the
    lint rule `rule` there. Only a lint finding can be suppressed: its source
    is `LINT_SOURCE`.

    The directive takes the finding line's indentation, so it stays in the
    block it annotates.
    """
    lines = text.splitlines()
    source = lines[line - 1] if 0 < line <= len(lines) else ''
    indent = source[: len(source) - len(source.lstrip())]
    return f'{indent}{_DIRECTIVE}{rule}\n'


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
            for table in _tables(fragment):
                for entity in table.values():
                    if entity.id == target:
                        return entity
    return next((base for base in raml.shapes if base.id == target), None)


def _tables(fragment: Library | APIFragment) -> tuple[Mapping[str, Any], ...]:
    return (
        fragment.types,
        fragment.annotation_types,
        fragment.traits,
        fragment.resource_types,
        fragment.security_schemes,
    )


# -- symbols ------------------------------------------------------------------------

_TABLE_KINDS: Final = (
    SymbolKind.TYPE,
    SymbolKind.ANNOTATION_TYPE,
    SymbolKind.TRAIT,
    SymbolKind.RESOURCE_TYPE,
    SymbolKind.SECURITY_SCHEME,
)


def document_symbols(snapshot: Snapshot, uri: str) -> list[Symbol]:
    """The file's outline: its declarations, its documentation items, and its
    resources with their methods, as written in this file.

    A method a resource type contributed is written in the resource type, not
    under the resource, and is not in the outline.
    """
    raml = snapshot.raml
    fragment = None if raml is None else raml.fragments.get(uri)
    if raml is None or fragment is None:
        return []
    found: list[Symbol] = []
    if isinstance(fragment, (Library, APIFragment)):
        for kind, table in zip(_TABLE_KINDS, _tables(fragment), strict=True):
            found += [
                symbol
                for name, entity in table.items()
                if (symbol := _symbol(name, kind, entity.location, entity.key_pos, entity.value_pos)) is not None
            ]
    if isinstance(fragment, APIFragment):
        for item in fragment.documentation:
            title = '' if item.title is None else str(item.title.value)
            symbol = _symbol(title, SymbolKind.DOCUMENTATION, item.location, item.key_pos, item.value_pos)
            if symbol is not None:
                found.append(symbol)
        found += _resources((e for e in raml.endpoints.values() if e.full_uri == e.uri), uri)
    return sorted(found, key=lambda symbol: (symbol.span.line, symbol.span.column))


def _resources(endpoints: Iterable[EndPoint], uri: str) -> list[Symbol]:
    found: list[Symbol] = []
    for endpoint in endpoints:
        symbol = _symbol(endpoint.uri, SymbolKind.RESOURCE, endpoint.location, endpoint.key_pos, endpoint.value_pos)
        if symbol is None or endpoint.location != uri:
            continue
        symbol.children += [
            child
            for method, operation in endpoint.operations.items()
            if (child := _symbol(method, SymbolKind.METHOD, operation.location, operation.key_pos, operation.value_pos))
            and operation.location == uri
            and _within(child.span, symbol.span)
        ]
        symbol.children += _resources(endpoint.endpoints.values(), uri)
        found.append(symbol)
    return found


def _within(inner: Position, outer: Position) -> bool:
    return (outer.line, outer.column) <= (inner.line, inner.column) and (inner.end_line, inner.end_column) <= (
        outer.end_line,
        outer.end_column,
    )


def _symbol(name: str, kind: SymbolKind, uri: str, key: Position | None, value: Position | None) -> Symbol | None:
    """A symbol from its key and its value, or `None` where the key has no position."""
    if key is None or not key.is_known:
        return None
    end = value if value is not None and value.is_known and value.end_line >= key.line else key
    span = Position(key.line, key.column, end.end_line, end.end_column)
    return Symbol(name, kind, uri, span, Position(key.line, key.column, key.line, key.end_column))


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
        for uri in raml.fragments:
            for symbol in document_symbols(snapshot, uri):
                key = (symbol.uri, symbol.selection.line, symbol.selection.column)
                if symbol.kind in _TABLE_KINDS and wanted in symbol.name.casefold() and key not in seen:
                    seen.add(key)
                    found.append(symbol)
    return found


# -- links, folding and selection -------------------------------------------------------


def links(snapshot: Snapshot, uri: str) -> list[Site]:
    """Each path to a file written in `uri`, as `Site(target, span)`.

    The target is the file the path resolves to, found or not.
    """
    raml = snapshot.raml
    if raml is None:
        return []
    # A template applied twice records its includes twice.
    found = list(
        dict.fromkeys(
            Site(ref.abs_uri, span)
            for ref in raml.include_refs.get(uri, ())
            if (span := _path_span(snapshot, uri, ref.position, ref.path)) is not None
        )
    )
    fragment = raml.fragments.get(uri)
    found += [
        Site(link.link.location, span)
        for link in ([] if fragment is None else fragment.uses.values())
        if link.link is not None and (span := _path_span(snapshot, uri, link.value_pos, link.value)) is not None
    ]
    return found


def _path_span(snapshot: Snapshot, uri: str, at: Position, path: str) -> Position | None:
    """The span of `path`, as the occurrence index placed it."""
    occurrences = snapshot.occurrences
    if occurrences is None or not at.is_known:
        return None
    for occurrence in occurrences.in_file(uri):
        if occurrence.role is Role.LINK and occurrence.written == path and at.line <= occurrence.line <= at.end_line:
            return occurrence.span
    return None


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
            if _holds(pair, line, column):
                found.append(pair)
                return key if _holds(key.full_position, line, column) else value
    elif node.kind is NodeKind.SEQUENCE:
        return next((item for item in node.content if _holds(item.full_position, line, column)), None)
    return None


def _holds(span: Position, line: int, column: int) -> bool:
    return (span.line, span.column) <= (line, column) <= (span.end_line, span.end_column)


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
    parents = [*base.inherits, *([] if base.alias is None else [base.alias])]
    return [symbol for parent in parents if (symbol := _type_symbol(parent)) is not None]


def subtypes(snapshot: Snapshot, item: Symbol) -> list[Symbol]:
    """The declared types that name `item` in their `type:`."""
    base = _declared(snapshot, item)
    raml = snapshot.raml
    if base is None or raml is None:
        return []
    found: list[Symbol] = []
    for fragment in raml.fragments.values():
        if isinstance(fragment, (Library, APIFragment)):
            for table in (fragment.types, fragment.annotation_types):
                for child in table.values():
                    parents = [*child.inherits, *([] if child.alias is None else [child.alias])]
                    if any(parent.id == base.id for parent in parents) and (symbol := _type_symbol(child)):
                        found.append(symbol)
    return found


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
    return None if not base.name else _symbol(base.name, kind, base.location, base.key_pos, base.value_pos)
