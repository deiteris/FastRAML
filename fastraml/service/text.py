"""Positions as a protocol counts them (docs/21 § 3).

fastRAML counts lines and columns from 1, in code points, with exclusive ends
(docs/11 § 1). A protocol counts both from 0, and columns in the units of its
position encoding: UTF-16 code units by default in LSP.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

__all__ = ['Encoding', 'Lines']

#: The line breaks LSP counts. YAML also breaks at NEL, LS and PS, so a line
#: holding one of those is numbered differently by the two (docs/21 § 3).
_BREAK: Final = re.compile('\r\n|[\n\r]')


class Encoding(StrEnum):
    """A position encoding: what a protocol's column counts."""

    UTF8 = 'utf-8'
    UTF16 = 'utf-16'
    UTF32 = 'utf-32'


class Lines:
    """One text's lines, for converting a column between fastRAML and a protocol."""

    __slots__ = ('_lines',)

    def __init__(self, text: str) -> None:
        self._lines = _BREAK.split(text)

    def __len__(self) -> int:
        return len(self._lines)

    def line(self, line: int) -> str:
        """The text of the 1-based `line`, or `''` past the end."""
        return self._lines[line - 1] if 0 < line <= len(self._lines) else ''

    def to_protocol(self, line: int, column: int, encoding: Encoding) -> tuple[int, int]:
        """A 1-based `line:column`, as fastRAML counts, as a protocol's 0-based pair."""
        prefix = self.line(line)[: column - 1]
        return line - 1, _units(prefix, encoding)

    def from_protocol(self, line: int, character: int, encoding: Encoding) -> tuple[int, int]:
        """A protocol's 0-based `line:character` as fastRAML's 1-based pair.

        A character inside a code point, or past the end of the line, lands on
        the code point that holds it, or on the line's end.
        """
        text = self.line(line + 1)
        if encoding is Encoding.UTF32 or text.isascii():
            return line + 1, min(character, len(text)) + 1
        codec = 'utf-8' if encoding is Encoding.UTF8 else 'utf-16-le'
        width = 1 if encoding is Encoding.UTF8 else 2
        data = text.encode(codec)[: character * width]
        return line + 1, len(data.decode(codec, errors='ignore')) + 1


def _units(text: str, encoding: Encoding) -> int:
    if encoding is Encoding.UTF32 or text.isascii():
        return len(text)
    if encoding is Encoding.UTF8:
        return len(text.encode('utf-8'))
    return len(text.encode('utf-16-le')) // 2
