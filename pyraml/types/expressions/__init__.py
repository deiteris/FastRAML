"""RAML type expressions (RDT): `Person`, `string[]`, `(A | B)[]`, `lib.T?`.

See docs/06-type-expressions.md.
"""

from __future__ import annotations

from .parser import (
    Array,
    ExprCache,
    Optional_,
    Primitive,
    RdtNode,
    Reference,
    Union,
    parse_expression,
)

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
