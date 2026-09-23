r"""Recursive-descent parser for RAML type expressions (RDT).

Implements docs/06-type-expressions.md § 1 and § 2: the grammar, the AST, and
the memoised entry point. Building shapes from the AST (docs/06 § 3) is in
`types/resolve.py`, because it is mutually recursive with resolution: a
reference's target may itself be unresolved.

Grammar (docs/06 § 1):

    entrypoint : expression EOF ;
    expression : union ;
    union      : type ( '|' type )* ;
    type       : ( primitive | group | reference ) '[]'* '?'? ;
    group      : '(' expression ')' ;
    primitive  : 'string' | 'integer' | 'number' | 'boolean' | 'datetime'
               | 'time-only' | 'datetime-only' | 'date-only' | 'file'
               | 'nil' | 'any' | 'array' | 'object' | 'union' ;
    reference  : IDENTIFIER ;
    IDENTIFIER : [0-9a-zA-Z_.-]+ ;
    WS         : [ \t]+ -> hidden ;

`IDENTIFIER` includes `.`, so a qualified name is one token and this parser
keeps its whole text. The resolver splits it on the last dot.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastraml.errors import RamlError

from .lexer import Token, TokenKind, expression_error, tokenize

__all__ = [
    'Array',
    'ExprCache',
    'Optional_',
    'Primitive',
    'RdtNode',
    'Reference',
    'Union',
    'parse_expression',
]


@dataclass(slots=True, frozen=True)
class Primitive:
    name: str
    col: int


@dataclass(slots=True, frozen=True)
class Reference:
    name: str
    col: int  # possibly dotted


@dataclass(slots=True, frozen=True)
class Array:
    item: RdtNode


@dataclass(slots=True, frozen=True)
class Optional_:  # noqa: N801 - trailing underscore avoids shadowing `typing.Optional`
    inner: RdtNode  # sugar for `inner | nil`


@dataclass(slots=True, frozen=True)
class Union:
    members: tuple[RdtNode, ...]


#: The AST produced by `parse_expression`. Only `Primitive` and `Reference`
#: carry a column, because only names are referenceable (docs/06 § 2).
RdtNode = Primitive | Reference | Array | Optional_ | Union

# Primitive keywords (grammar `primitive`), classified by exact identifier text
# after longest-match lexing, so `stringy` is a reference (docs/06 § 1).
_PRIMITIVES = frozenset(
    {
        'string',
        'integer',
        'number',
        'boolean',
        'datetime',
        'time-only',
        'datetime-only',
        'date-only',
        'file',
        'nil',
        'any',
        'array',
        'object',
        'union',
    }
)


class _Parser:
    """One function per production, walking a flat token list with one cursor."""

    __slots__ = ('_pos', '_tokens')

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def parse(self) -> RdtNode:
        """`entrypoint : expression EOF ;`"""
        node = self._union()
        trailing = self._peek()
        if trailing.kind is not TokenKind.EOF:
            raise expression_error(trailing.col, found=trailing.text)
        return node

    def _union(self) -> RdtNode:
        """`union : type ( '|' type )* ;` -- a single member is not a union."""
        members = [self._type()]
        while self._peek().kind is TokenKind.PIPE:
            self._advance()
            members.append(self._type())
        if len(members) == 1:
            return members[0]
        return Union(tuple(members))

    def _type(self) -> RdtNode:
        """`type : ( primitive | group | reference ) '[]'* '?'? ;`

        `[]` binds tighter than `|` because it is consumed here, inside a
        single union member, before control ever returns to `_union`. `?` is
        applied after every `[]`, so `string[]?` is an optional array rather
        than an array of optionals.
        """
        node = self._atom()
        while self._peek().kind is TokenKind.ARRAY:
            self._advance()
            node = Array(node)
        if self._peek().kind is TokenKind.QUESTION:
            self._advance()
            node = Optional_(node)
        return node

    def _atom(self) -> RdtNode:
        """`primitive | group | reference`."""
        token = self._peek()
        if token.kind is TokenKind.LPAREN:
            self._advance()
            node = self._union()
            closing = self._peek()
            if closing.kind is not TokenKind.RPAREN:
                raise expression_error(closing.col, expected=')', found=closing.text)
            self._advance()
            return node
        if token.kind is TokenKind.IDENT:
            self._advance()
            if token.text in _PRIMITIVES:
                return Primitive(token.text, token.col)
            return Reference(token.text, token.col)
        raise expression_error(token.col, expected='a type', found=token.text)


#: What `Raml.expr_cache` holds (docs/06 § 2).
ExprCache = dict[str, 'RdtNode | RamlError']


def parse_expression(text: str, cache: ExprCache) -> RdtNode:
    """Parse a RAML type expression (a `type:` scalar) to its AST.

    Memoised on the expression's exact text. The AST is immutable and carries
    only intra-expression columns, so one entry serves every occurrence of the
    text. A failed parse is cached as the `RamlError` itself and re-raised on
    every hit; each occurrence's file position comes from the caller.

    The cache is passed in because it belongs to one parse, like every other
    cache on `Raml` (docs/02 § 3).

    Raises `RamlError` for a malformed expression, with the offending token's
    0-based column in `info['column']`. See `expression_error` for why the
    error carries no file `location` at this layer.
    """
    cached = cache.get(text)
    if cached is None:
        try:
            cached = _Parser(tokenize(text)).parse()
        except RamlError as err:
            cached = err
        cache[text] = cached
    if isinstance(cached, RamlError):
        raise cached
    return cached
