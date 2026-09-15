r"""Recursive-descent parser for RAML type expressions (RDT).

Implements docs/06-type-expressions.md sections 1-2: the grammar, the AST, and
the memoised entry point. Section 3 (AST -> shapes) is **not** implemented
here. It is mutually recursive with the resolution driver -- a reference's
target may itself be unresolved -- so the two live together in
`types/resolve.py` rather than one importing the other (docs/02 section 2).

Grammar (docs/06 section 1):

    entrypoint : expression EOF ;
    expression : union ;
    union      : type ( '|' type )* ;
    type       : ( primitive | group | reference ) '[]'* '?'? ;
    group      : '(' expression ')' ;
    primitive  : 'string' | 'integer' | 'number' | 'boolean' | 'datetime'
               | 'time-only' | 'datetime-only' | 'date-only' | 'file'
               | 'nil' | 'any' | 'array' | 'object' | 'union' ;
    reference  : IDENTIFIER ( '.' IDENTIFIER )? ;
    IDENTIFIER : [0-9a-zA-Z_.-]+ ;
    WS         : [ \t]+ -> hidden ;

`IDENTIFIER` already includes `.`, so `reference`'s explicit dotted
alternative reaches the same token as a plain `IDENTIFIER` -- the tokenizer
never splits a dotted name, and this parser keeps the whole text. The
resolver (out of scope here) is what splits on the last dot.
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


#: The AST produced by `parse_expression`. Doc section 2.2 gives `col` only to
#: `Primitive` and `Reference` -- `Array`, `Optional_` and `Union` carry none
#: of their own, matching the dataclasses as written there. See this module's
#: docstring in the report back to the caller for the discrepancy against the
#: task description, which says every node carries a column.
RdtNode = Primitive | Reference | Array | Optional_ | Union

# Primitive keywords (grammar `primitive`), matched by exact identifier text.
# Doc section 1: "Primitive keywords are matched before IDENTIFIER" -- here
# that happens one layer up from the lexer, once the full (longest-match)
# identifier text is known, which is equivalent to ANTLR's priority-ordered
# token rules for every input the grammar accepts (see lexer.py).
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


#: What `Raml.expr_cache` holds (docs/06 section 2.3).
ExprCache = dict[str, 'RdtNode | RamlError']


def parse_expression(text: str, cache: ExprCache) -> RdtNode:
    """Parse a RAML type expression (a `type:` scalar) to its AST.

    Memoised on the expression's exact text. The AST is immutable and carries
    no file positions -- only intra-expression columns -- so one entry safely
    serves every occurrence of the same text in a corpus. A failed parse is
    cached as the `RamlError` instance itself and re-raised on every hit, so
    500 occurrences of one malformed expression cost one parse and 500
    dictionary hits, while each of the 500 diagnostics still gets its own file
    position -- that comes from the caller, not from here.

    The cache belongs to one parse and is passed in rather than held on this
    module. A module-level dict would outlive every `Raml` and grow for the
    life of the interpreter, and `Raml` already owns every other cache whose
    lifetime is the parse (docs/02 section 3).

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
