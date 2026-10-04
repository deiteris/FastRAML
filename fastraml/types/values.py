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
    from fastraml.types.base import BaseShape, ScalarFacet

__all__ = [
    'DATETIME_ONLY',
    'DATETIME_ONLY_PATTERN',
    'DATE_ONLY',
    'INTEGER_RANGES',
    'RFC2616_PATTERN',
    'TIME_ONLY',
    'TIME_ONLY_PATTERN',
    'EnumValues',
    'ValueSet',
    'as_exact',
    'as_fraction',
    'broken',
    'check_non_negative',
    'decimal_digits',
    'decimal_text',
    'failure',
    'index_path',
    'is_multiple_of',
    'is_subset',
    'key_path',
    'parse_rfc2616',
    'parse_rfc3339',
    'rejected',
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


def broken(message: str, facet: ScalarFacet[Any], *, info: dict[str, Any]) -> RamlError:
    """A value `facet` rejects, placed at the facet: its key and value, in the
    file that wrote them, which may be a parent's (docs/11 § 3).
    """
    return failure(message, facet.location, facet.key_pos.through(facet.value_pos), info=info)


def rejected(message: str, base: BaseShape, *, info: dict[str, Any]) -> RamlError:
    """A value `base` rejects on no one facet, placed at the name it is
    declared under, or at the declaration where it has none.
    """
    return failure(message, base.location, base.key_pos if base.key_pos.is_known else base.value_pos, info=info)


def check_non_negative(name: str, value: int, location: str, position: Position | None) -> RamlError | None:
    """The six length and count facets are non-negative (docs/10 § 2).

    Spec, per facet: "Value MUST be equal to or greater than 0." A negative
    bound is not merely unsatisfiable — `minLength: -2` accepts everything and
    reads as though it constrains something.

    Returns rather than raises: every caller is accumulating.
    """
    if value < 0:
        return failure('facet must not be negative', location, position, info={'facet': name})
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
    with `big.Rat.SetString(fmt.Sprintf("%v", v))`. The text is read by
    `Decimal`, whose `as_integer_ratio` is exact and, being C, half the cost of
    `Fraction` parsing it (`bench micro 'validate number'`).

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
        if not isfinite(value):
            return None
        return Fraction(*Decimal(repr(value)).as_integer_ratio())
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


def as_exact(value: Any) -> int | Fraction | None:
    """`as_fraction`, except that an `int` stays an `int`.

    An `int` is already exact, and comparing it with an `int` bound is one C
    operation where a `Fraction` pays four Python-level calls. `integer` bounds
    are `int`s, so validating an integer compares nothing else. `denominator`
    and `str` read the same on both types.
    """
    if type(value) is int:
        return value
    return as_fraction(value)


def is_multiple_of(value: int | Fraction, multiple: Fraction) -> bool:
    """Exactly, not approximately: the quotient's denominator must be 1."""
    if multiple == 0:
        # `check()` rejects `multipleOf: 0`, so this only guards a shape built
        # programmatically. Nothing is a multiple of zero.
        return False
    # a/b ÷ c/d is integral when a·d is divisible by b·c: integer arithmetic,
    # where dividing two `Fraction`s would reduce the quotient by a gcd first.
    return (value.numerator * multiple.denominator) % (value.denominator * multiple.numerator) == 0


def decimal_text(value: Fraction) -> str:
    """A `Fraction` as decimal text, without going through `float`.

    Numbers never pass through `float` (docs/10 § 5), and a value shown to a
    reader is no exception even though nothing compares it: `1.1` reaching a
    reader as `1.100000000000000088` would be a defect of the view, not of the
    parser. The graph, the tree, the renderer and the compatibility report all
    use this.
    """
    found = decimal_digits(value)
    if found is None:
        # Not representable as a terminating decimal, so it is reported exactly
        # as the ratio it is. `multipleOf: 1/3` cannot arise from RAML source,
        # which is decimal, but a merged bound could in principle.
        return f'{value.numerator}/{value.denominator}'
    digits, scale = found
    if scale == 0:
        return str(digits)
    text = str(abs(digits)).rjust(scale + 1, '0')
    sign = '-' if digits < 0 else ''
    return f'{sign}{text[:-scale]}.{text[-scale:]}'


def decimal_digits(value: Fraction) -> tuple[int, int] | None:
    """`(digits, scale)` with `value == digits * 10**-scale` and the least `scale >= 0`.

    `None` when *value* has no terminating decimal expansion: its reduced
    denominator has a prime factor other than 2 and 5. The one test of that,
    for every caller that writes a `Fraction` out as a decimal.
    """
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return None
    scale = max(twos, fives)
    return value.numerator * 2 ** (scale - twos) * 5 ** (scale - fives), scale


# -- dates (docs/10 § 5) -------------------------------------------------------

#: One grammar per kind, spelled once. The parser compiles it for its own check
#: and the views export it as a schema `pattern` (docs/16 § 8), so the two
#: cannot disagree. Day ranges, the leap-year rule, the clock's ranges and the
#: leap second `:60` (which RFC 3339 permits and RAML does not forbid) are all
#: in the grammar, so a match is the whole check. Digits are ASCII `[0-9]`:
#: Python's `\d` would also take Arabic-Indic and other Unicode digits.
#:
#: The spelling is the subset ECMA-262 and Python `re` read alike: ASCII
#: classes, non-capturing groups, `^` with no flags, and `(?![\s\S])` for the
#: end. Python's `$` also matches before a final newline, and `jsonschema`
#: applies a pattern with `re.search`, so a time followed by a newline would
#: pass.
_LEAP_YEAR: Final = r'(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|(?:[02468][048]|[13579][26])00)'
_DATE: Final = (
    r'(?:[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])'
    r'|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)|02-(?:0[1-9]|1[0-9]|2[0-8]))'
    rf'|{_LEAP_YEAR}-02-29)'
)
_CLOCK: Final = r'(?:[01][0-9]|2[0-3]):[0-5][0-9]:(?:[0-5][0-9]|60)'
_TIME: Final = rf'{_CLOCK}(?:\.[0-9]+)?'
_DAY_MONTH: Final = (
    r'(?:(?:0[1-9]|[12][0-9]|3[01]) (?:Jan|Mar|May|Jul|Aug|Oct|Dec)'
    r'|(?:0[1-9]|[12][0-9]|30) (?:Apr|Jun|Sep|Nov)'
    r'|(?:0[1-9]|1[0-9]|2[0-8]) Feb) [0-9]{4}'
    rf'|29 Feb {_LEAP_YEAR}'
)
_END: Final = r'(?![\s\S])'
TIME_ONLY_PATTERN: Final = rf'^{_TIME}{_END}'
DATETIME_ONLY_PATTERN: Final = rf'^{_DATE}T{_TIME}{_END}'
#: RFC 2616 section 3.3.1's preferred form, which is what `format: rfc2616`
#: means. The two obsolete forms the RFC also permits are not accepted, matching
#: go-raml. The weekday is not checked against the date.
RFC2616_PATTERN: Final = rf'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), (?:{_DAY_MONTH}) {_CLOCK} GMT{_END}'

#: Compiled once (docs/12 § 2). `date-only` and RFC 3339 export as a JSON
#: Schema `format`, so only their compiled form is needed.
DATE_ONLY: Final = re.compile(rf'^{_DATE}{_END}')
TIME_ONLY: Final = re.compile(TIME_ONLY_PATTERN)
DATETIME_ONLY: Final = re.compile(DATETIME_ONLY_PATTERN)
#: RFC 3339: `datetime-only` plus a mandatory offset, `Z` or `±hh:mm`.
_RFC3339: Final = re.compile(rf'^{_DATE}[Tt]{_TIME}(?:[Zz]|[+-][0-9]{{2}}:[0-9]{{2}}){_END}')
_RFC2616: Final = re.compile(RFC2616_PATTERN)


def valid_date_only(text: str) -> bool:
    return DATE_ONLY.match(text) is not None


def valid_time_only(text: str) -> bool:
    return TIME_ONLY.match(text) is not None


def valid_datetime_only(text: str) -> bool:
    return DATETIME_ONLY.match(text) is not None


def parse_rfc3339(text: str) -> bool:
    return _RFC3339.match(text) is not None


def parse_rfc2616(text: str) -> bool:
    return _RFC2616.match(text) is not None


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


class EnumValues(list[Any]):
    """An `enum` facet's members, which build their own `ValueSet` on first use.

    The index lives on the list rather than on the shape, so a shape without
    an `enum` carries nothing for it. Built once, it serves every membership
    test (docs/10 § 5). Valid because an enum is never edited in place:
    inheritance and the union intersection bind a new list (docs/07 § 4, § 5).
    """

    __slots__ = ('_index', '_span')

    def __init__(self, members: Iterable[Any] = ()) -> None:
        super().__init__(members)
        self._index: ValueSet | None = None
        self._span: Position | None = None

    def span(self) -> Position:
        """From the first member to the last: where a value outside them is
        reported against (docs/11 § 3.1). Once, since a union scan builds and
        drops that failure for every member it tries.
        """
        if self._span is None:
            self._span = self[0].value_pos.through(self[-1].value_pos)
        return self._span

    def contains(self, value: Any) -> bool:
        """Is `value` one of these members' raw values, under `same_value`?"""
        index = self._index
        if index is None:
            index = self._index = ValueSet(member.raw for member in self)
        return value in index


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
