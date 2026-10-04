"""Source positions.

Every entity the parser produces carries a position so that diagnostics can point
at the exact line and column that produced them. See docs/11-diagnostics.md.

All line and column numbers are **1-based**, matching YAML tooling and editor
conventions. End positions are **exclusive**: they name the character after the
last one in the span, so an editor can underline `column` through `end_column`
without an off-by-one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class Position:
    """A 1-based span within a single source file."""

    line: int
    column: int
    end_line: int = 0
    end_column: int = 0

    def __str__(self) -> str:
        return f'{self.line}:{self.column}'

    @property
    def is_known(self) -> bool:
        """Whether this position came from real source rather than a default.

        By value, not identity: a node built with no source, such as the
        synthetic key naming a DataType fragment's shape, spans `UNKNOWN`'s
        empty `1:1` too, and nothing is written there, on the header line.
        """
        if self.line == 1 and self.column == 1:
            return self.end_line != 1 or self.end_column != 1
        return self.line != 0 or self.column != 0

    def shifted(self, offset: int, length: int = 1) -> Position:
        """The `length` characters `offset` characters to the right of the start.

        Used for sub-token positioning: an error inside a URI template or a type
        expression is reported at its offset within the enclosing scalar,
        spanning the offending character or name.
        """
        column = self.column + offset
        return Position(line=self.line, column=column, end_line=self.line, end_column=column + length)

    def within(self, text: str) -> Position:
        """Where `text` starts inside the scalar this position spans.

        A quoted flow scalar on one line spans its text and two quotes, so the
        text starts one column in; a plain one starts where it is. A scalar
        whose span fits neither, such as one with an escape or a tag, is
        returned unchanged (docs/11 § 3).
        """
        if self.end_line == self.line and self.end_column - self.column == len(text) + 2:
            return Position(self.line, self.column + 1, self.line, self.end_column - 1)
        return self

    def through(self, last: Position) -> Position:
        """From this span's start to the end of `last`; `last` alone when this
        one is unknown.
        """
        if not self.is_known:
            return last
        return Position(self.line, self.column, last.end_line, last.end_column)

    def spanning(self, value: Position) -> Position:
        """The span of an entry keyed here with `value`, holding both, which
        `through` does not where the value starts first (a documentation
        item's title); this key alone when `value` is unknown.
        """
        return Position.covering((self, value)) if value.is_known else self

    def contains(self, inner: Position) -> bool:
        """Whether `inner` lies within this span, ends included."""
        return (self.line, self.column) <= (inner.line, inner.column) and (inner.end_line, inner.end_column) <= (
            self.end_line,
            self.end_column,
        )

    def holds(self, line: int, column: int) -> bool:
        """Whether the point `line:column` lies within this span, its end
        included: a cursor just after a token is on it, as an editor places it.
        """
        return (self.line, self.column) <= (line, column) <= (self.end_line, self.end_column)

    @staticmethod
    def covering(spans: Iterable[Position]) -> Position:
        """The least span holding every one of `spans`, of which there is one
        at least.
        """
        start = end = None
        for span in spans:
            if start is None or (span.line, span.column) < (start.line, start.column):
                start = span
            if end is None or (span.end_line, span.end_column) > (end.end_line, end.end_column):
                end = span
        if start is None or end is None:
            raise ValueError('no span to cover')
        return Position(start.line, start.column, end.end_line, end.end_column)


#: Used where a construct has no source of its own: a synthesised URI parameter,
#: a programmatically built shape, or a test fixture.
UNKNOWN: Final = Position(line=1, column=1, end_line=1, end_column=1)
