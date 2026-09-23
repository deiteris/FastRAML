"""`value_key` is `same_value` made hashable (docs/10 § 5).

`same_value` is the definition of semantic equality; `value_key` exists only so
that membership can be a set lookup. The two must agree in both directions,
over every kind of value a document can hold, or enum membership, enum
narrowing and `uniqueItems` stop agreeing with each other.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import example, given, settings
from hypothesis import strategies as st

from fastraml.types.values import _NoKey, is_subset, same_value, unique_items, value_key

#: Strings chosen to sit on both sides of the numeric-text screen: numbers in
#: the spellings `Fraction` accepts, and near misses that it rejects.
_TEXT = st.sampled_from(
    ['1', '1.0', ' 2', '-3', '+.5', '0.50', '1e3', '1000', '1/2', '2/4', '1_000', 'abc', '', ' ', '.', '-', 'e3', 'NaN']
)

_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-5, 5),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.sampled_from([0.5, 1.0, 2.2, 1e3, -0.0]),
    st.decimals(allow_nan=False, allow_infinity=True, places=2),
    # Quiet NaN only: a signalling one raises inside `same_value` itself, and no
    # decoder produces one.
    st.just(Decimal('NaN')),
    st.sampled_from([Decimal('1.0'), Decimal(1), Decimal('0.50')]),
    _TEXT,
    st.text(max_size=4),
)

_VALUES = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.sampled_from(['a', 'b', 'c']), children, max_size=3),
    ),
    max_leaves=6,
)


def _key(value):
    try:
        return value_key(value)
    except _NoKey:
        return None


#: Pairs at every boundary between kinds. Independent random draws almost never
#: meet here, so they are listed rather than left to chance.
_BOUNDARIES = [
    ('1', 1),
    ('1.0', 1),
    (' 2', 2),
    ('+.5', 0.5),
    ('2/4', 0.5),
    ('1e3', 1000),
    ('1_000', 1000),
    (Decimal('1.0'), 1),
    (Decimal('0.50'), 0.5),
    (2.2, Decimal('2.2')),
    (True, 1),
    (False, 0),
    (True, '1'),
    (None, 'null'),
    ('abc', 'abc'),
    ('NaN', float('nan')),
    (float('inf'), float('inf')),
    (float('inf'), Decimal('Infinity')),
    ({'a': 1}, {'a': 1.0}),
    ({'a': 1, 'b': 2}, {'b': 2, 'a': 1}),
    ([1, '2'], [1.0, 2]),
    ([1, 2], [2, 1]),
    ([True], [1]),
]


@settings(max_examples=2000)
@given(_VALUES, _VALUES)
@example(*_BOUNDARIES[0])
@example(*_BOUNDARIES[1])
@example(*_BOUNDARIES[2])
@example(*_BOUNDARIES[3])
@example(*_BOUNDARIES[4])
@example(*_BOUNDARIES[5])
@example(*_BOUNDARIES[6])
@example(*_BOUNDARIES[7])
@example(*_BOUNDARIES[8])
@example(*_BOUNDARIES[9])
@example(*_BOUNDARIES[10])
@example(*_BOUNDARIES[11])
@example(*_BOUNDARIES[12])
@example(*_BOUNDARIES[13])
@example(*_BOUNDARIES[14])
@example(*_BOUNDARIES[15])
@example(*_BOUNDARIES[16])
@example(*_BOUNDARIES[17])
@example(*_BOUNDARIES[18])
@example(*_BOUNDARIES[19])
@example(*_BOUNDARIES[20])
@example(*_BOUNDARIES[21])
@example(*_BOUNDARIES[22])
def test_equal_keys_exactly_when_same_value(left, right):
    left_key, right_key = _key(left), _key(right)
    if left_key is None or right_key is None:
        # A keyless value is compared by `same_value` itself; nothing to agree.
        return
    assert (left_key == right_key) == same_value(left, right)


@settings(max_examples=500)
@given(st.lists(_VALUES, max_size=8))
def test_unique_items_finds_the_first_semantic_duplicate(items):
    expected = next(
        (index for index, item in enumerate(items) if any(same_value(item, earlier) for earlier in items[:index])),
        None,
    )
    assert unique_items(items) == expected


@settings(max_examples=500)
@given(st.lists(_VALUES, max_size=6), st.lists(_VALUES, max_size=6))
def test_is_subset_is_membership_under_same_value(values, allowed):
    expected = all(any(same_value(value, member) for member in allowed) for value in values)
    assert is_subset(values, allowed) == expected


@given(
    st.fractions(min_value=-(10**6), max_value=10**6, max_denominator=8).filter(
        lambda value: value.denominator in (1, 2, 4, 8)
    )
)
def test_every_spelling_of_one_number_has_one_key(number):
    """`1`, `1.0`, `'1'`, `'1.0'` and `Decimal('1.0')` are one value (docs/10 § 5)."""
    decimal = Decimal(number.numerator) / Decimal(number.denominator)
    spellings = [float(number), str(decimal), decimal, f' {decimal}', str(number)]
    if number.denominator == 1:
        spellings.append(number.numerator)
    keys = {value_key(spelling) for spelling in spellings}
    assert len(keys) == 1, spellings
