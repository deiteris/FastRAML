"""Value machinery shared by declaration checking and instance validation.

A leaf of `types/`: it imports nothing else from the package. The per-kind
`check`/`validate` methods live on the kinds, and `types/validate.py` reaches
`complex_.py` through `types/unwrap.py`, so helpers kept in the driver would
make an import cycle.

No float comparison (numeric facets never pass through `float`) and no
per-character loops (compiled regexes do that work).

See docs/10-validation.md § 5.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from math import isfinite
from typing import TYPE_CHECKING, Any, Final

from fastraml.errors import ErrorKind, RamlError

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable

    from fastraml.positions import Position

__all__ = [
    'DATETIME_ONLY',
    'DATE_ONLY',
    'INTEGER_RANGES',
    'TIME_ONLY',
    'ValueSet',
    'as_fraction',
    'check_non_negative',
    'decimal_text',
    'failure',
    'index_path',
    'is_multiple_of',
    'is_subset',
    'key_path',
    'parse_rfc2616',
    'parse_rfc3339',
    'same_value',
    'type_name',
    'unique_items',
    'valid_date_only',
    'valid_datetime_only',
    'valid_time_only',
    'value_key',
]


# -- diagnostics ---------------------------------------------------------------


def failure(
    message: str,
    location: str,
    position: Position | None = None,
    *,
    info: dict[str, Any] | None = None,
) -> RamlError:
    """One validation diagnostic. `ErrorKind.VALIDATING` throughout P10."""
    return RamlError.new(message, location, position, kind=ErrorKind.VALIDATING, info=info)


def check_non_negative(name: str, value: int, location: str, position: Position | None) -> RamlError | None:
    """The six length and count facets are non-negative (docs/10 § 2).

    Spec, per facet: "Value MUST be equal to or greater than 0." A negative
    bound is not merely unsatisfiable — `minLength: -2` accepts everything and
    reads as though it constrains something.

    Returns rather than raises: every caller is accumulating.
    """
    if value < 0:
        return failure('facet must not be negative', location, position, info={'facet': name, 'value': value})
    return None


def key_path(path: str, key: str) -> str:
    """`$.address.zip`, built eagerly on the way down (docs/10 § 3)."""
    return f'{path}.{key}'


def index_path(path: str, index: int) -> str:
    """`$.items[3]`."""
    return f'{path}[{index}]'


#: What `info={'found': ...}` says about a value. `type(v).__name__` leaks
#: Python's spelling for the two that have a RAML name worth using.
_TYPE_NAMES: Final = {bool: 'boolean', type(None): 'nil'}


def type_name(value: Any) -> str:
    return _TYPE_NAMES.get(type(value), type(value).__name__)


# -- numbers (docs/10 § 5) -----------------------------------------------------

#: Inclusive bounds per `format` on an integer. These are the only formats an
#: integer accepts, and a number accepts none of them (docs/01 § 4.1).
INTEGER_RANGES: Final[dict[str, tuple[int, int]]] = {
    'int8': (-128, 127),
    'int16': (-32768, 32767),
    'int32': (-2147483648, 2147483647),
    'int': (-2147483648, 2147483647),
    'int64': (-(2**63), 2**63 - 1),
    'long': (-(2**63), 2**63 - 1),
}


def as_fraction(value: Any) -> Fraction | None:  # noqa: PLR0911 - one return per accepted type reads better than nesting
    """A number as an exact `Fraction`, or `None` if it is not a number.

    **Every conversion goes through decimal text, never through the binary
    value.** `2.2` in a document is the *text* `2.2`; the YAML decoder turned it
    into a float on the way in, and `float.as_integer_ratio()` would recover the
    binary approximation `2476979795053773/1125899906842624` rather than `11/5`.
    Since `multipleOf: 1.1` is built from its own raw text as `11/10`, the two
    would never divide evenly and `multipleOf: 1.1` would reject `2.2` — the
    exact failure the no-`float` rule exists to prevent (docs/10 § 5).

    `repr` gives the shortest decimal that round-trips to the same float, which
    is the author's text in every case that matters. go-raml does the same thing
    with `big.Rat.SetString(fmt.Sprintf("%v", v))`.

    `bool` is not a number here even though Python says it is a subclass of
    `int`; callers reject it before asking, and this is the second line of
    defence (docs/10 § 5).
    """
    if value is True or value is False:
        return None
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        # `inf` and `nan` have no decimal form; they are not RAML numbers.
        if value != value or value in (float('inf'), float('-inf')):  # noqa: PLR0124 - the NaN test
            return None
        return Fraction(repr(value))
    if isinstance(value, Decimal):
        try:
            return Fraction(str(value))
        except (ValueError, InvalidOperation):
            return None
    if isinstance(value, str):
        # A number-preserving decoder may hand a numeric string through, which
        # docs/10 § 5 accepts for `integer`.
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def is_multiple_of(value: Fraction, multiple: Fraction) -> bool:
    """Exactly, not approximately: the quotient's denominator must be 1."""
    if multiple == 0:
        # `check()` rejects `multipleOf: 0`, so this only guards a shape built
        # programmatically. Nothing is a multiple of zero.
        return False
    return (value / multiple).denominator == 1


def decimal_text(value: Fraction) -> str:
    """A `Fraction` as decimal text, without going through `float`.

    Numbers never pass through `float` (docs/10 § 5), and a value shown to a
    reader is no exception even though nothing compares it: `1.1` reaching a
    reader as `1.100000000000000088` would be a defect of the view, not of the
    parser. The graph, the renderer and the compatibility report all use this.
    """
    if value.denominator == 1:
        return str(value.numerator)
    residue = value.denominator
    for factor in (2, 5):
        while residue % factor == 0:
            residue //= factor
    if residue != 1:
        # Not representable as a terminating decimal, so it is reported exactly
        # as the ratio it is. `multipleOf: 1/3` cannot arise from RAML source,
        # which is decimal, but a merged bound could in principle.
        return f'{value.numerator}/{value.denominator}'
    digits = 0
    scaled = value
    while scaled.denominator != 1:
        scaled *= 10
        digits += 1
    text = str(abs(scaled.numerator)).rjust(digits + 1, '0')
    sign = '-' if scaled.numerator < 0 else ''
    return f'{sign}{text[:-digits]}.{text[-digits:]}'


# -- dates (docs/10 § 5) -------------------------------------------------------

#: Strict, anchored, and compiled once (docs/12 § 2).
DATE_ONLY: Final = re.compile(r'\A(\d{4})-(\d{2})-(\d{2})\Z')
TIME_ONLY: Final = re.compile(r'\A(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\Z')
DATETIME_ONLY: Final = re.compile(r'\A(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\Z')

#: RFC 3339: `datetime-only` plus a mandatory offset, `Z` or `±hh:mm`.
_RFC3339: Final = re.compile(
    r'\A(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})\Z'
)

#: RFC 2616 section 3.3.1's preferred form, which is what `format: rfc2616`
#: means. The two obsolete forms the RFC also permits are not accepted, matching
#: go-raml.
_RFC2616: Final = re.compile(
    r'\A(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), '
    r'(\d{2}) (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}) '
    r'(\d{2}):(\d{2}):(\d{2}) GMT\Z'
)

#: February holds 29 so the leap-year rule is the only special case below.
_DAYS_IN_MONTH: Final = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_FEBRUARY: Final = 2
_LEAP_DAY: Final = 29
_MONTHS: Final = 12


def _valid_date(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= _MONTHS or day < 1:
        return False
    if month == _FEBRUARY and day == _LEAP_DAY:
        return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return day <= _DAYS_IN_MONTH[month - 1]


def _valid_time(hour: int, minute: int, second: int) -> bool:
    # 60 is a leap second, which RFC 3339 permits and RAML does not forbid.
    return hour <= 23 and minute <= 59 and second <= 60  # noqa: PLR2004 - the clock's own numbers


def valid_date_only(text: str) -> bool:
    match = DATE_ONLY.match(text)
    return match is not None and _valid_date(*(int(part) for part in match.groups()))


def valid_time_only(text: str) -> bool:
    match = TIME_ONLY.match(text)
    return match is not None and _valid_time(*(int(part) for part in match.groups()))


def valid_datetime_only(text: str) -> bool:
    match = DATETIME_ONLY.match(text)
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    return _valid_date(year, month, day) and _valid_time(hour, minute, second)


def parse_rfc3339(text: str) -> bool:
    match = _RFC3339.match(text)
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    return _valid_date(year, month, day) and _valid_time(hour, minute, second)


def parse_rfc2616(text: str) -> bool:
    match = _RFC2616.match(text)
    if match is None:
        return False
    day, year, hour, minute, second = (int(part) for part in match.groups())
    month = 'JanFebMarAprMayJunJulAugSepOctNovDec'.index(text[8:11]) // 3 + 1
    return _valid_date(year, month, day) and _valid_time(hour, minute, second)


# -- semantic equality (docs/10 § 5) -----------------------------------------


def same_value(left: Any, right: Any) -> bool:  # noqa: PLR0911 - a chain of disjoint cases, not nested logic
    """Semantic equality: `1` and `1.0` are the same item, `1` and `True` are not.

    Used by `uniqueItems` and by enum membership, which have to agree: an enum
    of `[1]` accepting `1.0` while `uniqueItems` calls them distinct would be a
    contradiction the model cannot explain.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        return len(left) == len(right) and all(
            key in right and same_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_value(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict | list) or isinstance(right, dict | list):
        return False
    left_bool = left is True or left is False
    if left_bool != (right is True or right is False):
        # Guard Python's `True == 1`, which no RAML author means.
        return False
    if left_bool:
        return left is right
    left_number, right_number = as_fraction(left), as_fraction(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return type(left) is type(right) and left == right


#: What `Fraction` accepts from text begins like this (optional space and sign,
#: then a digit or a point and a digit). A string that does not is not a number,
#: and is keyed without paying for a failed parse.
_NUMBER_START: Final = re.compile(r'\s*[-+]?(?:\d|\.\d)')

#: Tags keep the three structured keys apart from each other and from a number.
_BOOL: Final = 'bool'
_MAP: Final = 'map'
_SEQ: Final = 'seq'
_OTHER: Final = 'other'


class _NoKey(Exception):  # noqa: N818 - a signal, not an error
    """A value `value_key` cannot represent: unhashable, or not equal to itself."""


def value_key(value: Any) -> Hashable:  # noqa: PLR0911 - one return per kind, as `same_value`
    """The hashable form of `value` under semantic equality.

    Two values have equal keys exactly when `same_value` calls them equal.
    Computed once per value, so membership is a C-level `set` lookup instead of
    one `same_value` call per member. `same_value` stays the definition;
    `tests/property` checks that the two agree in both directions.

    Raises `_NoKey` for a value it cannot represent (NaN, or an unhashable
    type YAML does not produce); `ValueSet` compares those with `same_value`.
    """
    if value is True or value is False:
        return (_BOOL, value)
    if isinstance(value, str):
        if _NUMBER_START.match(value) is None:
            return value
        number = as_fraction(value)
        return value if number is None else number
    if isinstance(value, dict):
        return (_MAP, frozenset((key, value_key(child)) for key, child in value.items()))
    if isinstance(value, list):
        return (_SEQ, tuple(value_key(child) for child in value))
    # Numbers are keyed by whichever exact type is cheapest to build. Python
    # compares and hashes `int`, `Decimal` and `Fraction` exactly and alike, so
    # `1`, `Decimal('1.0')` and `Fraction(1)` are one key. A float goes through
    # the same decimal text `as_fraction` reads (`repr`), never its binary value.
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value):
        return Decimal(repr(value))
    if isinstance(value, Decimal) and value.is_finite():
        return value
    # `same_value` falls back to `type(a) is type(b) and a == b`.
    if value != value:  # noqa: PLR0124 - NaN, which equals nothing, itself included
        raise _NoKey
    try:
        hash(value)
    except TypeError:
        raise _NoKey from None
    return (_OTHER, type(value), value)


class ValueSet:
    """A set of data values under `same_value`, so `1` and `1.0` are one member.

    Python's own `set` gets three things wrong for RAML data: `True` equals `1`,
    and neither a mapping nor a sequence is hashable, so a fallback to text
    would make `{a: 1, b: 2}` differ from `{b: 2, a: 1}` and `[1]` from `[1.0]`.
    Every question of the form "is this value one of those" goes through here or
    through `same_value`, so enum membership, enum narrowing and `uniqueItems`
    agree.

    Members are stored by `value_key`. The rare value without one is kept aside
    and compared with `same_value`; it can only equal another such value, since
    `same_value` then requires the same type.
    """

    __slots__ = ('_keyless', '_keys')

    def __init__(self, values: Iterable[Any] = ()) -> None:
        self._keys: set[Hashable] = set()
        self._keyless: list[Any] = []
        for value in values:
            self.add(value)

    def __contains__(self, value: Any) -> bool:
        try:
            return value_key(value) in self._keys
        except _NoKey:
            return any(same_value(value, member) for member in self._keyless)

    def add(self, value: Any) -> bool:
        """Add `value`; `False` if an equal member was already present."""
        try:
            key = value_key(value)
        except _NoKey:
            if any(same_value(value, member) for member in self._keyless):
                return False
            self._keyless.append(value)
            return True
        if key in self._keys:
            return False
        self._keys.add(key)
        return True


def unique_items(items: list[Any]) -> int | None:
    """The index of the first duplicate, or `None` if every item is distinct.

    Keys in a plain `set` first; `ValueSet` only if an item has no key.
    """
    seen: set[Hashable] = set()
    try:
        for index, item in enumerate(items):
            key = value_key(item)
            if key in seen:
                return index
            seen.add(key)
    except _NoKey:
        members = ValueSet()
        for index, item in enumerate(items):
            if not members.add(item):
                return index
    return None


def is_subset(values: Iterable[Any], allowed: Iterable[Any]) -> bool:
    """Is every one of `values` one of `allowed`, under `same_value`?

    Keys in a plain `set` first; `ValueSet` only if a value has no key.
    """
    values = list(values)
    allowed = list(allowed)
    try:
        keys = {value_key(value) for value in allowed}
        return all(value_key(value) in keys for value in values)
    except _NoKey:
        members = ValueSet(allowed)
        return all(value in members for value in values)
