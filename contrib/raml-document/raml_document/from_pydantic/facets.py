"""Constraints -> RAML facets, one declaration at a time.

`apply` writes what a list of constraints says onto a declaration that has a
single type. A union holds no facets of its own, so `Walk` splits one into its
members before calling here. What RAML has no facet for is handed to `drop`,
never approximated.
"""

from __future__ import annotations

import decimal
from typing import TYPE_CHECKING, Any

import annotated_types

if TYPE_CHECKING:
    from collections.abc import Callable

    from raml_document.model import TypeDecl

__all__ = ['Drop', 'apply']

#: `Walk.drop`: where, and what could not be carried.
type Drop = Callable[[str, str], None]

#: What `_general` reads off pydantic's own metadata objects; any other setting
#: on one is reported by name.
_READ = frozenset({'pattern', 'decimal_places', 'max_digits'})


def apply(decl: TypeDecl, metadata: list[Any], at: str, drop: Drop) -> None:
    """Write `metadata` onto `decl`.

    Length means different facets on different kinds -- `minLength` on a
    string, `minItems` on an array -- so the declaration decides, which it
    can because it was built first.
    """
    spelling = decl.type if isinstance(decl.type, str) else ''
    sequence = spelling == 'array' or spelling.endswith('[]')
    for item in metadata:
        match item:
            case annotated_types.Ge(ge=bound):
                decl.minimum = _number(bound, decl, at, drop)
            case annotated_types.Le(le=bound):
                decl.maximum = _number(bound, decl, at, drop)
            case annotated_types.Gt(gt=bound):
                drop(at, f'exclusive minimum {bound!r} has no RAML facet; not written')
            case annotated_types.Lt(lt=bound):
                drop(at, f'exclusive maximum {bound!r} has no RAML facet; not written')
            case annotated_types.MultipleOf(multiple_of=step):
                decl.multiple_of = _number(step, decl, at, drop)
            case annotated_types.MinLen(min_length=length):
                _length(decl, 'min', length, sequence=sequence)
            case annotated_types.MaxLen(max_length=length):
                _length(decl, 'max', length, sequence=sequence)
            case annotated_types.Len(min_length=low, max_length=high):
                _length(decl, 'min', low, sequence=sequence)
                _length(decl, 'max', high, sequence=sequence)
            case _:
                _general(decl, item, at, drop)


def _general(decl: TypeDecl, item: Any, at: str, drop: Drop) -> None:
    """Pydantic's own metadata objects, which carry several settings at once.

    Each setting is read or reported by name: `constr(to_upper=True)` carries a
    `maxLength` RAML has and a transformation it has not, and naming the whole
    object would hide the first.
    """
    if getattr(item, 'discriminator', None) is not None:
        return  # read by the walk, which reports what it cannot use
    pattern = getattr(item, 'pattern', None)
    if pattern is not None:
        decl.pattern = pattern
    places = getattr(item, 'decimal_places', None)
    if places is not None:
        # The facet JSON Schema cannot express: two decimal places is a step
        # of 0.01, which RAML states exactly.
        decl.multiple_of = float(decimal.Decimal(1).scaleb(-places))
    digits = getattr(item, 'max_digits', None)
    if digits is not None and places is not None:
        # A bound either way, and never looser than one already stated.
        bound = float(decimal.Decimal(10) ** (digits - places) - decimal.Decimal(1).scaleb(-places))
        decl.maximum = bound if decl.maximum is None else min(decl.maximum, bound)
        decl.minimum = -bound if decl.minimum is None else max(decl.minimum, -bound)
    elif digits is not None:
        drop(at, f'max_digits={digits} without decimal_places has no RAML facet; not written')
    rest = {key: value for key, value in getattr(item, '__dict__', {}).items() if value is not None}
    for key, value in rest.items():
        if key not in _READ:
            drop(at, f'{key}={value!r} has no RAML facet; not written')
    if not rest and pattern is None and places is None and digits is None:
        drop(at, f'no RAML facet for {item!r}')


def _length(decl: TypeDecl, end: str, length: int | None, *, sequence: bool) -> None:
    if length is not None:
        setattr(decl, f'{end}_{"items" if sequence else "length"}', length)


def _number(value: Any, decl: TypeDecl, at: str, drop: Drop) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    # A date bound, say: RAML has no minimum on a date.
    drop(at, f'bound {value!r} has no RAML facet on {decl.type}; not written')
    return None
