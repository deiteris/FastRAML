"""Where each name is written, and what it names (docs/16 § 9).

An occurrence is one name token in one file: a declaration's key, a type name
in an expression, an `is:` entry, an annotation's key, a path to a file. It
records the entity the name stands for by `id`, so a definition and its uses
meet on the id and nothing here resolves a name: every target is one the
passes bound.

Each candidate is checked by one law before it is kept: the retained source
text at its span is the name it records. For a use, that name is the one its
target is declared under, so the law catches a wrong position and a wrong
binding alike. A candidate that fails it is dropped and kept in `dropped`, so
the gaps behind it can be counted and fixed.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from fastraml.parser.fragments import APIFragment, Library
from fastraml.positions import Position
from fastraml.types.complex_ import ObjectShape

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from typing import Protocol

    from fastraml.parser.fragments import ReferenceResolver
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape

    class _Declared(Protocol):
        """A declaration, of any of the five kinds a fragment declares."""

        @property
        def id(self) -> int: ...
        @property
        def location(self) -> str: ...
        @property
        def key_pos(self) -> Position | None: ...


__all__ = ['Kind', 'Occurrence', 'Occurrences', 'Role', 'build_occurrences']


class Role(StrEnum):
    """What the name does where it is written."""

    DEFINITION = 'definition'
    REFERENCE = 'reference'
    #: The `lib` of `lib.User`, which names a `uses:` entry.
    ALIAS_PREFIX = 'alias_prefix'
    #: A primitive keyword, such as `string`. It has no target.
    BUILTIN = 'builtin'
    #: A path to a file: an `!include` argument or a `uses:` value.
    LINK = 'link'


class Kind(StrEnum):
    """What kind of entity the name stands for."""

    TYPE = 'type'
    ANNOTATION_TYPE = 'annotation_type'
    TRAIT = 'trait'
    RESOURCE_TYPE = 'resource_type'
    SECURITY_SCHEME = 'security_scheme'
    #: A `uses:` entry.
    LIBRARY = 'library'
    PROPERTY = 'property'
    #: A `facets:` entry.
    FACET = 'facet'
    FILE = 'file'


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One name token, where it is written and what it stands for."""

    #: The file the name is written in.
    uri: str
    #: The name token only: `User` in `lib.User`. On one line.
    span: Position
    role: Role
    kind: Kind
    #: The id of the entity it stands for. `None` for a built-in, and for a
    #: link to a file that decoded to no fragment.
    target: int | None
    #: The text at `span`.
    written: str


class Occurrences:
    """The occurrences of one parse, by file and by target."""

    __slots__ = ('_by_target', '_by_uri', '_starts', 'dropped')

    def __init__(self, kept: Sequence[Occurrence], dropped: Sequence[Occurrence]) -> None:
        self._by_uri: dict[str, list[Occurrence]] = {}
        self._by_target: dict[int, list[Occurrence]] = {}
        for occurrence in kept:
            self._by_uri.setdefault(occurrence.uri, []).append(occurrence)
            if occurrence.target is not None:
                self._by_target.setdefault(occurrence.target, []).append(occurrence)
        for found in self._by_uri.values():
            found.sort(key=_start)
        self._starts = {uri: [_start(occurrence) for occurrence in found] for uri, found in self._by_uri.items()}
        #: The candidates the law rejected, in the order they were met.
        self.dropped: tuple[Occurrence, ...] = tuple(dropped)

    def __repr__(self) -> str:
        kept = sum(len(found) for found in self._by_uri.values())
        return f'<Occurrences kept={kept} dropped={len(self.dropped)}>'

    def in_file(self, uri: str) -> Sequence[Occurrence]:
        """The occurrences written in `uri`, in source order."""
        return self._by_uri.get(uri, ())

    def at(self, uri: str, line: int, column: int) -> list[Occurrence]:
        """The occurrences whose span holds `line:column`.

        More than one only where one span names several entities.
        """
        starts = self._starts.get(uri, [])
        end = bisect_right(starts, (line, column))
        if not end:
            return []
        # Tokens do not overlap, so only the nearest start can hold the cursor.
        same = self._by_uri[uri][bisect_left(starts, starts[end - 1]) : end]
        return [found for found in same if found.span.line == line and column < found.span.end_column]

    def of(self, target: int) -> Sequence[Occurrence]:
        """Every occurrence of the entity `target`, its definition among them."""
        return self._by_target.get(target, ())


def build_occurrences(raml: Raml) -> Occurrences:
    """The occurrences of a model parsed with `ParseOptions(retain_text=True)`.

    Built from what the parse bound, so a lenient model gives the occurrences
    of the stages it completed. One span met again, as a template applied
    twice meets it, is kept once for each target.
    """
    if not raml.retain_text:
        msg = 'occurrences are checked against the source text: parse with ParseOptions(retain_text=True)'
        raise ValueError(msg)
    law = _Law(raml.source_texts)
    seen: set[tuple[str, Position, int | None]] = set()
    kept: list[Occurrence] = []
    dropped: list[Occurrence] = []
    for occurrence in _candidates(raml):
        key = (occurrence.uri, occurrence.span, occurrence.target)
        if key in seen:
            continue
        seen.add(key)
        (kept if law.holds(occurrence) else dropped).append(occurrence)
    return Occurrences(kept, dropped)


def _start(occurrence: Occurrence) -> tuple[int, int]:
    return occurrence.span.line, occurrence.span.column


#: A line break as YAML reads one, so line numbers agree with the composer's.
_LINE_BREAK: Final = re.compile('\r\n|[\n\r\x85\u2028\u2029]')


class _Law:
    """Whether the source text at an occurrence's span is what it records."""

    __slots__ = ('_lines', '_texts')

    def __init__(self, texts: Mapping[str, str]) -> None:
        self._texts = texts
        self._lines: dict[str, list[str]] = {}

    def holds(self, occurrence: Occurrence) -> bool:
        lines = self._lines.get(occurrence.uri)
        if lines is None:
            text = self._texts.get(occurrence.uri)
            if text is None:
                return False
            lines = self._lines[occurrence.uri] = _LINE_BREAK.split(text)
        span = occurrence.span
        if not 0 < span.line <= len(lines):
            return False
        return lines[span.line - 1][span.column - 1 : span.end_column - 1] == occurrence.written


# -- candidates -----------------------------------------------------------------


def _candidates(raml: Raml) -> Iterator[Occurrence]:
    yield from _declarations(raml)
    yield from _shapes(raml)
    yield from _applications(raml)
    yield from _annotations(raml)
    yield from _includes(raml)


def _token(  # noqa: PLR0913 - an occurrence's fields, and where the token starts
    uri: str, at: Position | None, written: str, *, role: Role, kind: Kind, target: int | None, offset: int = 0
) -> Iterator[Occurrence]:
    """The occurrence of `written`, starting `offset` characters into `at`.

    Nothing for a position the source never gave.
    """
    if at is None or not at.is_known or not written:
        return
    column = at.column + offset
    yield Occurrence(uri, Position(at.line, column, at.line, column + len(written)), role, kind, target, written)


def _qualified(  # noqa: PLR0913 - a reference's fields, and the scope its prefix names a `uses:` entry in
    uri: str,
    at: Position,
    name: str,
    *,
    declared: str,
    kind: Kind,
    target: int,
    resolver: ReferenceResolver | None,
    offset: int = 0,
) -> Iterator[Occurrence]:
    """A reference written as `name`, which is `declared` or `lib.declared`.

    Split by the declared name rather than at a dot: `oauth2.0` is one name.
    """
    prefix = name[: -len(declared) - 1] if name != declared and name.endswith(f'.{declared}') else ''
    if prefix:
        link = None if resolver is None else resolver.library_link(prefix)
        if link is not None:
            yield from _token(uri, at, prefix, role=Role.ALIAS_PREFIX, kind=Kind.LIBRARY, target=link.id, offset=offset)
        offset += len(prefix) + 1
    yield from _token(uri, at, declared, role=Role.REFERENCE, kind=kind, target=target, offset=offset)


def _path(uri: str, at: Position, path: str, target: int | None) -> Iterator[Occurrence]:
    """A path to a file, placed by where its node ends.

    A node's position starts at its tag, and `uses:` may write `!include`
    too, so the path is the text that finishes the node.
    """
    if at.end_line == at.line:
        start = Position(at.line, at.end_column - len(path))
        yield from _token(uri, start, path, role=Role.LINK, kind=Kind.FILE, target=target)


def _declarations(raml: Raml) -> Iterator[Occurrence]:
    """Every declared name, and each `uses:` entry with the file it links."""
    for fragment in raml.fragments.values():
        if isinstance(fragment, (Library, APIFragment)):
            declared: list[tuple[Kind, Mapping[str, _Declared]]] = [
                (Kind.TYPE, fragment.types),
                (Kind.ANNOTATION_TYPE, fragment.annotation_types),
                (Kind.TRAIT, fragment.traits),
                (Kind.RESOURCE_TYPE, fragment.resource_types),
                (Kind.SECURITY_SCHEME, fragment.security_schemes),
            ]
            for kind, table in declared:
                for name, entity in table.items():
                    yield from _token(
                        entity.location, entity.key_pos, name, role=Role.DEFINITION, kind=kind, target=entity.id
                    )
        for alias, link in fragment.uses.items():
            yield from _token(
                link.location, link.key_pos, alias, role=Role.DEFINITION, kind=Kind.LIBRARY, target=link.id
            )
            if link.link is not None:
                yield from _path(link.location, link.value_pos, link.value, link.link.id)


def _shapes(raml: Raml) -> Iterator[Occurrence]:
    """Each shape's properties and `facets:` entries, and the names in its
    type expression, which P7 recorded (docs/06 § 3).
    """
    for base in raml.shapes:
        members = [(Kind.FACET, name, prop.base) for name, prop in base.custom_facet_defs.items()]
        if isinstance(base.shape, ObjectShape):
            members += [(Kind.PROPERTY, name, prop.base) for name, prop in (base.shape.properties or {}).items()]
        for kind, name, member in members:
            yield from _token(member.location, member.key_pos, name, role=Role.DEFINITION, kind=kind, target=member.id)
        for ref in base.type_expr_refs:
            at = Position(ref.line, ref.column)
            if ref.builtin is not None:
                yield from _token(base.location, at, ref.builtin, role=Role.BUILTIN, kind=Kind.TYPE, target=None)
            elif ref.library_link is not None and ref.library_alias is not None:
                yield from _token(
                    base.location,
                    at,
                    ref.library_alias,
                    role=Role.ALIAS_PREFIX,
                    kind=Kind.LIBRARY,
                    target=ref.library_link.id,
                )
            elif ref.resolved is not None and ref.resolved.name:
                yield from _token(
                    base.location,
                    at,
                    ref.resolved.name,
                    role=Role.REFERENCE,
                    kind=_type_kind(ref.resolved),
                    target=ref.resolved.id,
                )


def _type_kind(base: BaseShape) -> Kind:
    return Kind.ANNOTATION_TYPE if base.is_annotation_type else Kind.TYPE


def _applications(raml: Raml) -> Iterator[Occurrence]:
    """Every `type:`, `is:` and `securedBy:` name that P4 and P5 bound."""
    schemes = [*raml.global_secured_by]
    for endpoint in raml.endpoints.values():
        applied = [(Kind.TRAIT, ref) for ref in endpoint.traits]
        if endpoint.resource_type is not None:
            applied.append((Kind.RESOURCE_TYPE, endpoint.resource_type))
        schemes += endpoint.secured_by
        for operation in endpoint.operations.values():
            applied += [(Kind.TRAIT, ref) for ref in operation.traits]
            schemes += operation.secured_by
        for kind, ref in applied:
            if ref.resolved is not None:
                yield from _qualified(
                    ref.location,
                    ref.key_pos,
                    ref.name,
                    declared=ref.resolved.name,
                    kind=kind,
                    target=ref.resolved.id,
                    resolver=None if ref.scope is None else ref.scope.anchor,
                )
    for scheme in schemes:
        if scheme.definition is not None and not scheme.is_null:
            yield from _qualified(
                scheme.location,
                scheme.value_pos,
                scheme.name,
                declared=scheme.definition.name,
                kind=Kind.SECURITY_SCHEME,
                target=scheme.definition.id,
                resolver=raml.resolver_at(scheme.location),
            )


def _annotations(raml: Raml) -> Iterator[Occurrence]:
    """The name in each `(annotation)` key P8 bound, past the `(`."""
    for extension in raml.domain_extensions:
        defined_by = extension.defined_by
        if defined_by is not None and defined_by.name:
            yield from _qualified(
                extension.location,
                extension.key_pos,
                extension.name,
                declared=defined_by.name,
                kind=_type_kind(defined_by),
                target=defined_by.id,
                resolver=extension.anchor,
                offset=1,
            )


def _includes(raml: Raml) -> Iterator[Occurrence]:
    """Each `!include` argument, linked to the fragment it decoded to if any."""
    for refs in raml.include_refs.values():
        for ref in refs:
            fragment = raml.fragments.get(ref.abs_uri)
            yield from _path(ref.source_uri, ref.position, ref.path, None if fragment is None else fragment.id)
