"""Value machinery shared by declaration checking and instance validation.

A leaf of `types/`: it imports `base.py` and nothing else from the package. That
is deliberate. The per-kind `check`/`validate` methods live on the kinds, and
the pass driver in `types/validate.py` reaches `types/unwrap.py` through
`_ensure_unwrapped` — which reaches `complex_.py`. Helpers kept in the driver
would close that loop, so they are here instead
(docs/02-architecture.md section 2).

Two rules from CLAUDE.md are enforced by what this module does *not* offer:
there is no float comparison, because numeric facets never pass through
`float`; and there is no per-character loop, because a compiled regex does the
same work in C.

See docs/10-validation.md sections 5.2 and 5.3.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

from pyraml.errors import ErrorKind, RamlError

if TYPE_CHECKING:
    from pyraml.positions import Position

__all__ = [
    'DATETIME_ONLY',
    'DATE_ONLY',
    'INTEGER_RANGES',
    'TIME_ONLY',
    'as_fraction',
    'check_non_negative',
    'failure',
    'index_path',
    'is_multiple_of',
    'key_path',
    'parse_rfc2616',
    'parse_rfc3339',
    'same_value',
    'type_name',
    'unique_items',
    'valid_date_only',
    'valid_datetime_only',
    'valid_time_only',
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
    """The six length and count facets are non-negative (docs/10 section 2).

    Spec, per facet: "Value MUST be equal to or greater than 0." A negative
    bound is not merely unsatisfiable — `minLength: -2` accepts everything and
    reads as though it constrains something.

    Returns rather than raises: every caller is accumulating.
    """
    if value < 0:
        return failure('facet must not be negative', location, position, info={'facet': name, 'value': value})
    return None


def key_path(path: str, key: str) -> str:
    """`$.address.zip` — built eagerly on the way down (docs/10 section 3)."""
    return f'{path}.{key}'


def index_path(path: str, index: int) -> str:
    """`$.items[3]`."""
    return f'{path}[{index}]'


#: What `info={'found': ...}` says about a value. `type(v).__name__` leaks
#: Python's spelling for the two that have a RAML name worth using.
_TYPE_NAMES: Final = {bool: 'boolean', type(None): 'nil'}


def type_name(value: Any) -> str:
    return _TYPE_NAMES.get(type(value), type(value).__name__)


# -- numbers (docs/10 section 5.3) ---------------------------------------------

#: Inclusive bounds per `format` on an integer. Deviation D2: these are the only
#: formats an integer accepts, and a number accepts none of them.
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
    exact failure the no-`float` rule exists to prevent (docs/10 section 5.3).

    `repr` gives the shortest decimal that round-trips to the same float, which
    is the author's text in every case that matters. go-raml does the same thing
    with `big.Rat.SetString(fmt.Sprintf("%v", v))`.

    `bool` is not a number here even though Python says it is a subclass of
    `int`; callers reject it before asking, and this is the second line of
    defence (docs/10 section 5).
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
        # doc 10 section 5 accepts for `integer`.
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


# -- dates (docs/10 section 5) -------------------------------------------------

#: Strict, anchored, and compiled once. go-raml scans these by hand, which is
#: correct in Go and slow here (CLAUDE.md, docs/12 section 12).
DATE_ONLY: Final = re.compile(r'\A(\d{4})-(\d{2})-(\d{2})\Z')
TIME_ONLY: Final = re.compile(r'\A(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\Z')
DATETIME_ONLY: Final = re.compile(r'\A(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\Z')

#: RFC 3339: `datetime-only` plus a mandatory offset, `Z` or `±hh:mm`.
_RFC3339: Final = re.compile(
    r'\A(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})\Z'
)

#: RFC 2616 section 3.3.1's preferred form, which is what `format: rfc2616`
#: means. The two obsolete forms the RFC also permits are not accepted, matching
#: the reference implementation.
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


# -- uniqueItems (docs/10 section 5.2) -----------------------------------------

#: Below this many items, pairwise comparison beats hashing: it allocates
#: nothing, and n² is small.
PAIRWISE_LIMIT: Final = 20


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


def _hash(value: Any) -> int:
    """A hash agreeing with `same_value`: key-sorted for mappings, ordered for sequences."""
    if isinstance(value, dict):
        return hash(('map', tuple(sorted((key, _hash(child)) for key, child in value.items()))))
    if isinstance(value, list):
        return hash(('seq', tuple(_hash(child) for child in value)))
    if value is True or value is False:
        return hash(('bool', value))
    number = as_fraction(value)
    if number is not None:
        # Tagged as one type so `1` and `1.0` land in the same bucket.
        return hash(('num', number))
    return hash(('other', type(value).__name__, value))


def unique_items(items: list[Any]) -> int | None:
    """The index of the first duplicate, or `None` if every item is distinct.

    Two strategies by size, as in go-raml. The hashed path resolves collisions
    by full comparison, so it never reports a duplicate that is not one.
    """
    if len(items) <= PAIRWISE_LIMIT:
        for index, item in enumerate(items):
            for earlier_index in range(index):
                if same_value(item, items[earlier_index]):
                    return index
        return None

    buckets: dict[int, list[Any]] = {}
    for index, item in enumerate(items):
        bucket = buckets.setdefault(_hash(item), [])
        if any(same_value(item, earlier) for earlier in bucket):
            return index
        bucket.append(item)
    return None
