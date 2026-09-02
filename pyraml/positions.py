"""Source positions.

Every entity the parser produces carries a position so that diagnostics can point
at the exact line and column that produced them. See docs/11-diagnostics.md.

All line and column numbers are **1-based**, matching YAML tooling and editor
conventions. End positions are **exclusive**: they name the character after the
last one in the span, so an editor can underline `column` through `end_column`
without an off-by-one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final


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
        """Whether this position came from real source rather than a default."""
        return self is not UNKNOWN and (self.line, self.column) != (0, 0)

    def shifted(self, offset: int) -> Position:
        """Return this position moved `offset` characters to the right.

        Used for sub-token positioning: an error inside a URI template or a type
        expression is reported at its byte offset within the enclosing scalar.
        The result spans a single character, which is what an editor needs to
        mark one offending symbol.
        """
        column = self.column + offset
        return Position(line=self.line, column=column, end_line=self.line, end_column=column + 1)

    def with_end(self, end_line: int, end_column: int) -> Position:
        return replace(self, end_line=end_line, end_column=end_column)


#: Used where a construct has no source of its own: a synthesised URI parameter,
#: a programmatically built shape, or a test fixture.
UNKNOWN: Final = Position(line=1, column=1, end_line=1, end_column=1)
