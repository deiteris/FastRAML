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
from typing import TYPE_CHECKING, Any, Final

from fastraml.errors import RamlError
from fastraml.parser.fragments import LibraryLink, every_declaration
from fastraml.positions import Position
from fastraml.types.base import BaseShape
from fastraml.views.occurrences import DECLARATION_KINDS as _OCCURRENCE_KINDS
from fastraml.views.occurrences import Kind, Link, Role
from fastraml.views.tree import build_tree
from fastraml.yamlnode import Node, NodeKind, compose, pairs

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from fastraml.errors import Trace
    from fastraml.parser.fragments import Declaration
    from fastraml.registry import Raml
    from fastraml.service.workspace import Snapshot
    from fastraml.views.authored import Placed
    from fastraml.views.lint import Finding
    from fastraml.views.occurrences import Occurrence

__all__ = [
    'DECLARATION_KINDS',
    'Diagnostic',
    'Related',
    'Site',
    'Symbol',
    'SymbolKind',
    'definition',
    'detail_line',
    'diagnostics',
    'folding_ranges',
    'highlights',
    'hover',
    'links',
    'references',
    'selection_ranges',
    'subtypes',
    'supertypes',
    'suppression',
    'symbol',
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
    placed = [(frame, frame.position) for frame in chain if frame.position is not None and frame.position.is_known]
    inner, span = placed[-1] if placed else (chain[-1], _START)
    # The outer frames not at the reported site, then every frame's origin:
    # the constraint a value broke.
    related = tuple(
        Related(Site(frame.location, position), frame.rendered_message())
        for frame, position in placed[:-1]
        if (frame.location, position) != (inner.location, span)
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
        if isinstance(occurrence, Link):
            found.append(Site(occurrence.resolved, _START))
        elif occurrence.target is not None:
            found += [_site(other) for other in occurrences.of(occurrence.target) if other.role is Role.DEFINITION]
    if not found and snapshot.hover is not None:
        for target in snapshot.hover.data_at(uri, line, column):
            base = target.base
            if base.key_pos.is_known:
                found.append(Site(base.location, base.key_pos.within(base.name or target.name)))
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
    """Explain the source token and, where available, its effective meaning."""
    context = snapshot.hover
    return None if context is None else context.at(uri, line, column)


def _entity(raml: Raml, target: int) -> Declaration | LibraryLink | None:
    """The declaration, `uses:` entry or shape with the id `target`."""
    for fragment in raml.fragments.values():
        for link in fragment.uses.values():
            if link.id == target:
                return link
    for _key, _name, entity in every_declaration(raml):
        if entity.id == target:
            return entity
    return next((base for base in raml.shapes if base.id == target), None)


# -- symbols ------------------------------------------------------------------------

#: The symbol kind of each declaration table, by the key it is written under.
DECLARATION_KINDS: Final = {key: SymbolKind(kind) for key, kind in _OCCURRENCE_KINDS.items()}


def detail_line(text: str) -> str:
    """The first line of `text`, cut to `_DETAIL_LIMIT` characters."""
    line = text.strip().partition('\n')[0]
    return line if len(line) <= _DETAIL_LIMIT else f'{line[: _DETAIL_LIMIT - 1]}…'


_DETAIL_LIMIT: Final = 80


def symbol(
    name: str, kind: SymbolKind, placed: Placed, detail: str = '', *, key: Position | None = None
) -> Symbol | None:
    """A symbol selecting `placed`'s key, or `key`, and spanning key and
    value; `None` where the key has no position.

    The span holds the key, which a client requires of the selection: a
    documentation item has no key and selects its title, inside its value.
    """
    key = placed.key_pos if key is None else key
    if key is None or not key.is_known:
        return None
    return Symbol(name, kind, placed.location, key.spanning(placed.value_pos), key, detail=detail_line(detail))


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
        for table, name, entity in every_declaration(raml):
            each = symbol(name, DECLARATION_KINDS[table], entity) if wanted in name.casefold() else None
            if each is None:
                continue
            key = (each.uri, each.selection.line, each.selection.column)
            if key not in seen:
                seen.add(key)
                found.append(each)
    return found


# -- links, folding and selection -------------------------------------------------------


def links(snapshot: Snapshot, uri: str) -> list[Site]:
    """Each path to a file written in `uri`, as `Site(target, span)`.

    The target is the file the path resolves to, found or not.
    """
    occurrences = snapshot.occurrences
    if occurrences is None:
        return []
    return [
        Site(occurrence.resolved, occurrence.span)
        for occurrence in occurrences.in_file(uri)
        if isinstance(occurrence, Link)
    ]


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
    return [each for parent in _parents(base) if (each := _type_symbol(parent)) is not None]


def subtypes(snapshot: Snapshot, item: Symbol) -> list[Symbol]:
    """The declared types that name `item` in their `type:`."""
    base = _declared(snapshot, item)
    raml = snapshot.raml
    if base is None or raml is None:
        return []
    return [
        each
        for _key, _name, child in every_declaration(raml)
        if isinstance(child, BaseShape)
        and any(parent.id == base.id for parent in _parents(child))
        and (each := _type_symbol(child)) is not None
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
    return None if not base.name else symbol(base.name, kind, base)
