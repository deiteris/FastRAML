"""Tokenizer for RAML type expressions (RDT).

See docs/06-type-expressions.md section 2.1. One compiled alternation drives a
single `finditer` pass; there is no per-character Python loop, per
docs/12-performance.md section 12.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from pyraml.errors import ErrorKind, RamlError
from pyraml.positions import Position

__all__ = [
    'Token',
    'TokenKind',
    'expression_error',
    'tokenize',
]


class TokenKind(Enum):
    LPAREN = auto()
    RPAREN = auto()
    PIPE = auto()
    ARRAY = auto()
    QUESTION = auto()
    IDENT = auto()
    EOF = auto()


@dataclass(slots=True, frozen=True)
class Token:
    """One lexical token. `col` is 0-based within the expression string."""

    kind: TokenKind
    text: str
    col: int


# One compiled alternation, matched with `finditer` instead of a per-character
# loop. `ARRAY` ('[]') is tried before the catch-all `BAD` group, so a lone '['
# with no matching ']' falls through to `BAD` rather than being silently
# swallowed. `IDENT` is greedy over the whole `IDENTIFIER` character class, so
# it always wins the longest match against a keyword prefix -- exactly
# ANTLR's max-munch rule (`stringy` lexes as one IDENTIFIER, never as
# STRING_TYPE followed by `y`). Keyword classification therefore happens one
# layer up, in the parser, once the full identifier text is known.
_TOKEN_RE = re.compile(
    r'(?P<LPAREN>\()'
    r'|(?P<RPAREN>\))'
    r'|(?P<PIPE>\|)'
    r'|(?P<ARRAY>\[\])'
    r'|(?P<QUESTION>\?)'
    r'|(?P<IDENT>[0-9A-Za-z_.-]+)'
    r'|(?P<WS>[ \t]+)'
    r'|(?P<BAD>.)'
)

_KIND_BY_GROUP = {
    'LPAREN': TokenKind.LPAREN,
    'RPAREN': TokenKind.RPAREN,
    'PIPE': TokenKind.PIPE,
    'ARRAY': TokenKind.ARRAY,
    'QUESTION': TokenKind.QUESTION,
    'IDENT': TokenKind.IDENT,
}

#: The one message every RDT diagnostic uses. Per docs/11-diagnostics.md
#: section 6, values that vary between occurrences (the column, the offending
#: text) go in `info`, never interpolated into the message.
_MESSAGE = 'invalid type expression'


def expression_error(col: int, **info: object) -> RamlError:
    """Build the one diagnostic every RDT failure raises.

    `col` is 0-based, matching every other column in this package. There is no
    file `location` at this layer -- `parse_expression` is memoised on bare
    text and knows nothing about which file, or how many files, an expression
    came from (docs/06-type-expressions.md section 2.3). The caller that owns
    a real position is expected to catch this error and rebuild one with its
    own `location` and a column rebased by `base.type_expr.value_pos.column`;
    until that caller exists (doc section 3, out of scope here) the raw error
    still carries a usable 1-based `Position` for direct callers and tests.
    """
    return RamlError.new(
        _MESSAGE,
        '',
        Position(1, col + 1),
        kind=ErrorKind.PARSING,
        info={'column': col, **info},
    )


def tokenize(text: str) -> list[Token]:
    """Tokenize a type expression, skipping whitespace.

    Raises `RamlError` at the column of the first character that matches no
    token in the grammar (a lone `[` or `]`, or anything outside the
    `IDENTIFIER` character set and the four punctuation tokens). The returned
    list always ends with a `TokenKind.EOF` token whose column is `len(text)`,
    so the parser can report "expected X" at the position where the missing
    token should have been.
    """
    tokens: list[Token] = []
    for match in _TOKEN_RE.finditer(text):
        group = match.lastgroup
        if group == 'WS':
            continue
        if group == 'BAD':
            raise expression_error(match.start(), character=match.group())
        if group is None:  # pragma: no cover - the alternation always names a group
            raise expression_error(match.start())
        tokens.append(Token(_KIND_BY_GROUP[group], match.group(), match.start()))
    tokens.append(Token(TokenKind.EOF, '', len(text)))
    return tokens
