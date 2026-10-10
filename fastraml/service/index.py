"""Lazy snapshot lookups over parser-bound declarations (docs/21 § 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastraml.parser.fragments import LibraryLink, every_declaration
from fastraml.parser.security import SecuritySchemeDefinition
from fastraml.parser.templates import TemplateDefinition
from fastraml.types.base import BaseShape

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.parser.fragments import Declaration
    from fastraml.registry import Raml

type Entity = BaseShape | LibraryLink | TemplateDefinition | SecuritySchemeDefinition


def parents_of(base: BaseShape) -> Iterator[BaseShape]:
    """The direct parent edges the parser records, including an alias's referent."""
    for parent in base.inherits:
        # A multiple-inheritance scalar is a positioned wrapper whose key is
        # the expression itself. Navigate its bound alias to the declaration;
        # a genuinely declared alias has a separate authored key and stays named.
        if parent.alias is not None and parent.type_expr is not None and parent.key_pos == parent.type_expr.position:
            yield parent.alias
        else:
            yield parent
    if base.alias is not None:
        yield base.alias


class SemanticIndex:
    """Query-specific caches; constructing this owner enumerates no model data."""

    __slots__ = ('_by_id', '_by_uri', '_children', '_declarations', '_raml')

    def __init__(self, raml: Raml) -> None:
        self._raml = raml
        self._declarations: tuple[tuple[str, str, Declaration], ...] | None = None
        self._by_id: dict[int, Entity] | None = None
        self._by_uri: dict[str, list[tuple[str, str, Declaration]]] | None = None
        self._children: dict[int, list[BaseShape]] | None = None

    @property
    def declarations(self) -> tuple[tuple[str, str, Declaration], ...]:
        if self._declarations is None:
            self._declarations = tuple(every_declaration(self._raml))
        return self._declarations

    @property
    def by_id(self) -> dict[int, Entity]:
        if self._by_id is None:
            found: dict[int, Entity] = {}
            for base in self._raml.shapes:
                found.setdefault(base.id, base)
            for _, _, entity in self.declarations:
                found[entity.id] = entity
            for fragment in self._raml.fragments.values():
                found.update((link.id, link) for link in fragment.uses.values())
            self._by_id = found
        return self._by_id

    @property
    def by_uri(self) -> dict[str, list[tuple[str, str, Declaration]]]:
        if self._by_uri is None:
            found: dict[str, list[tuple[str, str, Declaration]]] = {}
            for entry in self.declarations:
                found.setdefault(entry[2].location, []).append(entry)
            self._by_uri = found
        return self._by_uri

    @property
    def children(self) -> dict[int, list[BaseShape]]:
        if self._children is None:
            found: dict[int, list[BaseShape]] = {}
            for _, _, entity in self.declarations:
                if isinstance(entity, BaseShape):
                    for parent in {parent.id for parent in parents_of(entity)}:
                        found.setdefault(parent, []).append(entity)
            self._children = found
        return self._children
