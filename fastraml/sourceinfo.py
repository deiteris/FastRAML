"""Facts a decoder records about how a document was written.

A `KeywordUse` names where a deprecated keyword was written; a `WrittenSection`
where an entity wrote a section key, such as `types:` or a method's `headers:`.
Each is a model fact the registry stores and a view may read: not a
diagnostic, and it keeps no YAML node (docs/04 § 5, docs/05 § 3, docs/21 § 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastraml.positions import Position


@dataclass(frozen=True, slots=True, eq=False)
class KeywordUse:
    """An accepted compatibility spelling, not a lint finding or retained tree."""

    location: str
    position: Position
    name: str


@dataclass(frozen=True, slots=True, eq=False)
class WrittenSection:
    """A section key one entity wrote in its own file, and where the entry
    it opens ends. Held per file, so the record carries its owner's ID.

    `name` is the key as written (`schemas`, not `types`), or for a resource
    an Extension restated, the resource's full path.
    """

    owner: int
    name: str
    key: Position
    end_line: int
    end_column: int

    @property
    def span(self) -> Position:
        """From the key through the end of its value."""
        return Position(self.key.line, self.key.column, self.end_line, self.end_column)
