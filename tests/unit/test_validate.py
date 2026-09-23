"""`validate(value)` — does *data* conform to a declaration?

docs/10-validation.md § 3 to § 5. One test per row of the § 5 table, plus
three hazards worth naming: `bool` must not pass as
`integer`, `multipleOf: 1.1` must be exact, and `uniqueItems` must behave the
same either side of the n=20 strategy switch.

`tests/unit/test_check.py` is the other half — declaration consistency.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fastraml import ParseOptions, RamlError, parse_from_path
from fastraml.types.values import ValueSet, same_value, unique_items

API = '#%RAML 1.0\ntitle: T\n'


def declared_in(workspace, body: str, name: str = 'T'):
    """The parse and the named type from `types:\n<body>`, unwrapped."""
    root = workspace({'api.raml': API + 'types:\n' + body})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    return raml, raml.types_in(raml.location)[name]


def declared(workspace, body: str, name: str = 'T'):
    """The named type from `types:\n<body>`, parsed and unwrapped."""
    return declared_in(workspace, body, name)[1]


def parse_validating(workspace, body: str):
    root = workspace({'api.raml': API + 'types:\n' + body})
    try:
        parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True))
    except RamlError as err:
        return err
    return None


def messages(error: RamlError) -> set[str]:
    return {trace.message for chain in error.chains() for trace in chain}


class TestScalarTypes:
    @pytest.mark.parametrize(
        ('body', 'good', 'bad'),
        [
            ('  T: nil\n', None, 1),
            ('  T: boolean\n', True, 'true'),
            ('  T: string\n', 'x', 1),
            ('  T: integer\n', 4, 'x'),
            ('  T: number\n', 4.5, 'x'),
            ('  T: date-only\n', '2026-09-04', '2026-13-01'),
            ('  T: time-only\n', '12:30:00', '25:00:00'),
            ('  T: datetime-only\n', '2026-09-04T12:30:00', '2026-09-04 12:30:00'),
            ('  T: datetime\n', '2026-09-04T12:30:00Z', '2026-09-04T12:30:00'),
        ],
        ids=['nil', 'boolean', 'string', 'integer', 'number', 'date', 'time', 'datetime-only', 'datetime'],
    )
    def test_each_kind_accepts_its_own_and_refuses_others(self, workspace, body, good, bad):
        shape = declared(workspace, body)
        assert shape.validate(good) is None
        assert shape.validate(bad) is not None

    def test_any_accepts_everything(self, workspace):
        shape = declared(workspace, '  T: any\n')
        assert all(shape.validate(value) is None for value in (None, 1, 'x', [1], {'a': 1}))

    def test_a_leap_day_is_accepted_only_in_a_leap_year(self, workspace):
        shape = declared(workspace, '  T: date-only\n')
        assert shape.validate('2024-02-29') is None
        assert shape.validate('2025-02-29') is not None

    def test_rfc2616_is_selected_by_format(self, workspace):
        shape = declared(workspace, '  T:\n    type: datetime\n    format: rfc2616\n')
        assert shape.validate('Sun, 28 Feb 2010 16:00:49 GMT') is None
        # The two grammars share nothing: choosing one refuses the other.
        assert shape.validate('2026-09-04T12:30:00Z') is not None


class TestBooleanIsNotAnInteger:
    """`bool` is a subclass of `int` in Python. Missing this is a real bug."""

    @pytest.mark.parametrize('kind', ['integer', 'number'])
    def test_true_does_not_validate_as_a_number(self, workspace, kind):
        assert declared(workspace, f'  T: {kind}\n').validate(True) is not None

    def test_one_does_not_validate_as_a_boolean(self, workspace):
        assert declared(workspace, '  T: boolean\n').validate(1) is not None

    def test_a_boolean_is_not_an_enum_member_of_one(self, workspace):
        assert declared(workspace, '  T:\n    type: integer\n    enum: [1]\n').validate(True) is not None


class TestNumericExactness:
    """docs/10 § 5: nothing goes through `float`."""

    def test_multiple_of_a_decimal_is_exact(self, workspace):
        # Both sides go through decimal text. `2.2` as an exact binary ratio is
        # 2476979795053773/1125899906842624, which `11/10` does not divide.
        shape = declared(workspace, '  T:\n    type: number\n    multipleOf: 1.1\n')
        assert shape.validate(2.2) is None
        assert shape.validate(2.3) is not None

    def test_a_decoded_float_is_read_as_its_written_decimal(self, workspace):
        from fastraml.types.values import as_fraction

        assert as_fraction(2.2) == Fraction(11, 5)

    def test_bounds_are_compared_exactly(self, workspace):
        shape = declared(workspace, '  T:\n    type: number\n    minimum: 0.1\n    maximum: 0.3\n')
        assert shape.validate(0.2) is None
        assert shape.validate(0.4) is not None

    def test_an_integral_float_is_an_integer(self, workspace):
        shape = declared(workspace, '  T: integer\n')
        assert shape.validate(4.0) is None
        assert shape.validate(4.5) is not None

    def test_the_declared_bound_never_became_a_float(self, workspace):
        shape = declared(workspace, '  T:\n    type: number\n    minimum: 0.1\n')
        assert shape.shape.minimum.value == Fraction(1, 10)

    @pytest.mark.parametrize(
        ('value', 'text'),
        [
            (Fraction(3), '3'),
            (Fraction(11, 10), '1.1'),
            (Fraction(-1, 20), '-0.05'),
            (Fraction(1, 3), '1/3'),
        ],
    )
    def test_a_bound_is_shown_as_its_exact_decimal(self, value, text):
        # docs/10 § 5: a view shows `1.1`, never `1.100000000000000088`,
        # and a ratio with no terminating decimal as the ratio it is.
        from fastraml.types.values import decimal_text

        assert decimal_text(value) == text

    def test_a_format_range_is_enforced(self, workspace):
        shape = declared(workspace, '  T:\n    type: integer\n    format: int8\n')
        assert shape.validate(127) is None
        assert shape.validate(128) is not None


class TestString:
    def test_length_bounds(self, workspace):
        shape = declared(workspace, '  T:\n    type: string\n    minLength: 2\n    maxLength: 4\n')
        assert shape.validate('abc') is None
        assert shape.validate('a') is not None
        assert shape.validate('abcde') is not None

    def test_a_pattern_is_a_search_not_a_full_match(self, workspace):
        """The author writes the anchors; the parser does not add them.

        The spec never says `pattern:` is anchored, and writes `^...$` itself
        wherever it means anchored — `^.+@.+\\..+$`, `^\\d+\\-\\w+$`,
        `^\\w{16}$` — which would be noise if it were. go-raml agrees, using
        Go's unanchored `MatchString` (docs/10 § 5).
        """
        assert declared(workspace, '  T:\n    type: string\n    pattern: b\n').validate('abc') is None
        assert declared(workspace, '  T:\n    type: string\n    pattern: a.c\n').validate('abc') is None
        assert declared(workspace, '  T:\n    type: string\n    pattern: z\n').validate('abc') is not None

    def test_an_author_written_anchor_is_what_makes_it_whole(self, workspace):
        assert declared(workspace, '  T:\n    type: string\n    pattern: ^abc$\n').validate('abc') is None
        assert declared(workspace, '  T:\n    type: string\n    pattern: ^b\n').validate('abc') is not None
        assert declared(workspace, '  T:\n    type: string\n    pattern: ^a.c$\n').validate('xabcx') is not None

    def test_a_pattern_property_name_is_still_matched_unanchored(self, workspace):
        # The other direction, and the reason the change is not global: a
        # `/regex/` key is matched *against* a property name rather than
        # describing one, and `/^x/` is how they are written (docs/05 § 4).
        shape = declared(workspace, '  T:\n    properties:\n      /^x/: integer\n')
        assert shape.validate({'xylophone': 1}) is None
        assert shape.validate({'xylophone': 'no'}) is not None


class TestArray:
    def test_item_counts(self, workspace):
        shape = declared(workspace, '  T:\n    type: array\n    items: integer\n    minItems: 1\n    maxItems: 2\n')
        assert shape.validate([1]) is None
        assert shape.validate([]) is not None
        assert shape.validate([1, 2, 3]) is not None

    def test_every_element_is_validated(self, workspace):
        error = declared(workspace, '  T: integer[]\n').validate([1, 'x'])
        assert error is not None

    def test_the_failing_index_is_in_the_path(self, workspace):
        error = declared(workspace, '  T: integer[]\n').validate([1, 2, 'x'])
        paths = {trace.info.get('path') for chain in error.chains() for trace in chain if trace.info}
        assert '$[2]' in paths

    def test_unique_items(self, workspace):
        shape = declared(workspace, '  T:\n    type: array\n    items: integer\n    uniqueItems: true\n')
        assert shape.validate([1, 2, 3]) is None
        assert shape.validate([1, 2, 1]) is not None


class TestUniqueItems:
    """docs/10 § 5 — two strategies, one meaning."""

    def test_the_two_strategies_agree_across_the_switch(self, workspace):
        # The set hashes once it holds more than 20 members, so the duplicate
        # at 22 is found by hash and the one at 21 pairwise. A difference here
        # would be invisible in normal use and wrong in exactly one size range.
        for size in (21, 22):
            distinct = list(range(size))
            assert unique_items(distinct) is None
            duplicated = [*distinct[:-1], distinct[0]]
            assert unique_items(duplicated) == size - 1

    def test_equality_is_semantic_not_pythonic(self):
        assert same_value(1, 1.0)
        assert same_value({'a': 1}, {'a': 1.0})
        assert same_value([1, 2], [1.0, 2.0])
        # Python says `True == 1`; no RAML author means that.
        assert not same_value(1, True)

    def test_mapping_order_does_not_matter_but_sequence_order_does(self):
        assert same_value({'a': 1, 'b': 2}, {'b': 2, 'a': 1})
        assert not same_value([1, 2], [2, 1])

    def test_semantic_duplicates_are_found_by_both_strategies(self):
        assert unique_items([1, 1.0]) == 1
        assert unique_items([{'a': 1}, {'a': 1.0}]) == 1
        assert unique_items([*range(25), 1.0]) == 25

    @pytest.mark.parametrize('padding', [0, 25], ids=['pairwise', 'hashed'])
    def test_value_set_membership_is_semantic_in_both_strategies(self, padding):
        members = ValueSet([*(f'pad{index}' for index in range(padding)), 1, [1], {'a': 1, 'b': 2}])
        assert 1.0 in members
        assert [1.0] in members
        assert {'b': 2, 'a': 1} in members
        assert True not in members


class TestObject:
    def test_a_missing_required_property_fails(self, workspace):
        shape = declared(workspace, '  T:\n    properties:\n      a: string\n      b?: string\n')
        assert shape.validate({'a': 'x'}) is None
        assert shape.validate({'b': 'x'}) is not None

    def test_every_missing_property_is_named_in_one_message(self, workspace):
        # docs/10 § 5: one message listing all of them, not one each.
        shape = declared(workspace, '  T:\n    properties:\n      a: string\n      b: string\n      c: string\n')
        error = shape.validate({})
        missing = [
            trace.info['properties']
            for chain in error.chains()
            for trace in chain
            if trace.message == 'missing required properties'
        ]
        assert missing == [['a', 'b', 'c']]

    def test_declared_properties_are_checked_in_declaration_order(self, workspace):
        # The first error a caller sees should match the order the document
        # reads in, which makes the diagnostic reproducible.
        shape = declared(workspace, '  T:\n    properties:\n      a: integer\n      b: integer\n')
        error = shape.validate({'b': 'x', 'a': 'y'})
        paths = [trace.info['path'] for chain in error.chains() for trace in chain if trace.info.get('path')]
        assert paths.index('$.a') < paths.index('$.b')

    def test_additional_properties_are_allowed_by_default(self, workspace):
        assert declared(workspace, '  T:\n    properties:\n      a: string\n').validate({'a': 'x', 'z': 1}) is None

    def test_additional_properties_can_be_forbidden(self, workspace):
        shape = declared(workspace, '  T:\n    additionalProperties: false\n    properties:\n      a: string\n')
        assert shape.validate({'a': 'x'}) is None
        assert shape.validate({'a': 'x', 'z': 1}) is not None

    def test_a_pattern_property_takes_the_extra_key(self, workspace):
        shape = declared(workspace, '  T:\n    properties:\n      /^n_/: integer\n')
        assert shape.validate({'n_1': 5}) is None
        assert shape.validate({'n_1': 'x'}) is not None

    def test_declaring_a_pattern_makes_the_set_of_them_exhaustive(self, workspace):
        """Spec § Property Declarations, per its own examples' comments.

        `additionalProperties` defaults to true, so this reads backwards until
        you notice that `additional-properties.raml` writes the empty pattern
        `//` to "force all additional properties to be a string" — which is only
        worth writing if a non-empty pattern restricts what is allowed.
        """
        shape = declared(workspace, '  T:\n    properties:\n      a: string\n      /^n_/: integer\n')
        assert shape.validate({'a': 'x', 'n_1': 5}) is None
        assert shape.validate({'a': 'x', 'z': 5}) is not None

    def test_the_empty_pattern_lets_everything_through(self, workspace):
        shape = declared(workspace, '  T:\n    properties:\n      a: string\n      //: string\n')
        assert shape.validate({'a': 'x', 'anything': 'y'}) is None
        assert shape.validate({'a': 'x', 'anything': 5}) is not None

    def test_an_object_with_no_patterns_still_allows_extras(self, workspace):
        assert declared(workspace, '  T:\n    properties:\n      a: string\n').validate({'a': 'x', 'z': 1}) is None

    def test_property_counts(self, workspace):
        shape = declared(workspace, '  T:\n    type: object\n    minProperties: 1\n    maxProperties: 2\n')
        assert shape.validate({'a': 1}) is None
        assert shape.validate({}) is not None
        assert shape.validate({'a': 1, 'b': 2, 'c': 3}) is not None

    def test_a_nested_failure_reports_its_path(self, workspace):
        shape = declared(
            workspace,
            '  T:\n    properties:\n      address:\n        properties:\n          zip: integer\n',
        )
        error = shape.validate({'address': {'zip': 'x'}})
        paths = {trace.info.get('path') for chain in error.chains() for trace in chain if trace.info}
        assert '$.address.zip' in paths


class TestUnion:
    def test_the_first_matching_member_wins(self, workspace):
        shape = declared(workspace, '  T: string | integer\n')
        assert shape.validate('x') is None
        assert shape.validate(1) is None

    def test_no_match_reports_every_member(self, workspace):
        # Reporting only the last member's failure leaves the reader unable to
        # tell which one they meant to satisfy.
        error = declared(workspace, '  T: string | integer\n').validate([])
        assert error is not None
        expected = {
            trace.info.get('expected') for chain in error.chains() for trace in chain if trace.message == 'invalid type'
        }
        assert expected == {'string', 'integer'}


TAGGED = (
    '  Pet:\n    type: object\n    discriminator: kind\n    properties:\n      kind: string\n'
    '  Cat:\n    type: Pet\n    properties:\n      meows: boolean\n'
    '  Dog:\n    type: Pet\n    properties:\n      barks: boolean\n'
    '  T: Cat | Dog\n'
)


class TestUnionDispatchesOnADiscriminator:
    """docs/05 § 6 — a union of types that discriminate the same way.

    The spec makes this a MAY (`raml-10.md:762`): a processor "MAY provide an
    implementation that automatically selects a concrete type from a set of
    possible types". fastRAML takes it up (docs/01 § 4.5), and it narrows — a payload whose
    tag names no member is refused even where a member accepts it structurally.
    """

    def test_the_tag_selects_the_member(self, workspace):
        shape = declared(workspace, TAGGED)
        assert shape.validate({'kind': 'Cat', 'meows': True}) is None
        assert shape.validate({'kind': 'Dog', 'barks': True}) is None

    def test_an_unknown_tag_is_refused_by_name(self, workspace):
        error = declared(workspace, TAGGED).validate({'kind': 'Fish', 'meows': True})
        assert error is not None
        trace = next(t for chain in error.chains() for t in chain if t.message == 'unknown discriminator value')
        assert trace.info['discriminator'] == 'kind'
        assert trace.info['value'] == 'Fish'
        assert trace.info['known'] == ['Cat', 'Dog']

    def test_the_selected_member_reports_its_own_failure(self, workspace):
        # A scan reports 'value matches no member of the union' with every
        # member's complaint attached, which does not say `Cat` was the one meant.
        error = declared(workspace, TAGGED).validate({'kind': 'Cat', 'meows': 'yes'})
        assert error is not None
        assert 'value matches no member of the union' not in messages(error)
        assert 'invalid type' in messages(error)

    def test_a_tag_belonging_to_another_member_does_not_pass(self, workspace):
        # The narrowing dispatch makes: `Cat` has no required property `Dog` lacks, so
        # a linear scan accepts this payload against `Cat`.
        assert declared(workspace, TAGGED).validate({'kind': 'Dog', 'meows': True}) is not None

    def test_discriminator_value_overrides_the_type_name(self, workspace):
        body = TAGGED.replace('  Cat:\n    type: Pet\n', '  Cat:\n    type: Pet\n    discriminatorValue: cat\n')
        shape = declared(workspace, body)
        assert shape.validate({'kind': 'cat', 'meows': True}) is None
        assert shape.validate({'kind': 'Cat', 'meows': True}) is not None

    def test_an_absent_tag_falls_back_to_the_linear_scan(self, workspace):
        # Whether the property was required is the members' own rule, and they
        # state it better than a dispatch failure would.
        error = declared(workspace, TAGGED).validate({'meows': True})
        assert error is not None
        assert 'value matches no member of the union' in messages(error)

    @pytest.mark.parametrize(
        ('body', 'why'),
        [
            ('  A:\n    discriminator: k\n    properties:\n      k: string\n  T: A | string\n', 'a member is scalar'),
            (
                (
                    '  A:\n    discriminator: k\n    properties:\n      k: string\n'
                    '  B:\n    discriminator: j\n    properties:\n      j: string\n  T: A | B\n'
                ),
                'two discriminator names',
            ),
            (
                # One name, two types under it, so one key function cannot serve
                # both: `A` keys numerically and would read `B`'s `'1'` as `1`.
                (
                    '  A:\n    discriminator: k\n    properties:\n      k: integer\n'
                    '    discriminatorValue: 1\n'
                    '  B:\n    discriminator: k\n    properties:\n      k: string\n'
                    '    discriminatorValue: "1"\n  T: A | B\n'
                ),
                'one name, two property types',
            ),
            (
                (
                    '  P:\n    discriminator: k\n    properties:\n      k: string\n'
                    '  A:\n    type: P\n    discriminatorValue: same\n'
                    '  B:\n    type: P\n    discriminatorValue: same\n  T: A | B\n'
                ),
                'two members claim one value',
            ),
        ],
    )
    def test_no_table_is_built_when_the_members_disagree(self, workspace, body, why):
        from fastraml.types.complex_ import UnionShape

        shape = declared(workspace, body)
        assert isinstance(shape.shape, UnionShape)
        assert shape.shape.dispatch() is None, why

    def test_two_members_claiming_one_value_is_an_error(self, workspace):
        # Spec § Type Declarations: `discriminatorValue` "is unique in the
        # hierarchy of the type". Within a union it is also what makes the union
        # usable — no payload can say which of the two it is.
        error = parse_validating(
            workspace,
            '  P:\n    discriminator: k\n    properties:\n      k: string\n'
            '  A:\n    type: P\n    discriminatorValue: same\n'
            '  B:\n    type: P\n    discriminatorValue: same\n  T: A | B\n',
        )
        assert error is not None
        trace = next(
            t
            for chain in error.chains()
            for t in chain
            if t.message == 'discriminator value is claimed by more than one member of the union'
        )
        assert trace.info['discriminator'] == 'k'
        assert trace.info['values'] == ['same']

    def test_the_default_value_collides_with_an_explicit_one(self, workspace):
        # `discriminatorValue` defaults to the type's name, so `B` claiming `A`
        # collides with `A`'s own default. Nothing in either declaration says so.
        error = parse_validating(
            workspace,
            '  P:\n    discriminator: k\n    properties:\n      k: string\n'
            '  A:\n    type: P\n'
            '  B:\n    type: P\n    discriminatorValue: A\n  T: A | B\n',
        )
        assert error is not None
        assert 'discriminator value is claimed by more than one member of the union' in messages(error)

    def test_a_numeric_tag_is_keyed_by_value_not_by_spelling(self, workspace):
        # An `integer` property accepts 1, 1.0, '1' and '1.0' as one value
        # (docs/10 § 5), so all four have to select the member claiming `1`.
        shape = declared(
            workspace,
            '  P:\n    discriminator: k\n    properties:\n      k: integer\n'
            '  A:\n    type: P\n    discriminatorValue: 1\n    properties:\n      a?: string\n'
            '  B:\n    type: P\n    discriminatorValue: 2\n    properties:\n      b?: string\n  T: A | B\n',
        )
        for tag in (1, 1.0, '1', '1.0'):
            assert shape.validate({'k': tag, 'a': 'x'}) is None, tag
        assert shape.validate({'k': 3, 'a': 'x'}) is not None

    def test_a_string_tag_is_not_read_as_a_number(self, workspace):
        # '1' and '1.0' are two different strings. Keying them numerically merges
        # them and rejects this document as a collision — a false rejection, and
        # the reason the key is chosen by the property's declared type.
        assert (
            parse_validating(
                workspace,
                '  P:\n    discriminator: k\n    properties:\n      k: string\n'
                '  A:\n    type: P\n    discriminatorValue: "1"\n    properties:\n      a?: string\n'
                '  B:\n    type: P\n    discriminatorValue: "1.0"\n    properties:\n      b?: string\n  T: A | B\n',
            )
            is None
        )

    def test_a_string_tag_selects_by_its_exact_spelling(self, workspace):
        shape = declared(
            workspace,
            '  P:\n    discriminator: k\n    properties:\n      k: string\n'
            '  A:\n    type: P\n    discriminatorValue: "1"\n    properties:\n      a: string\n'
            '  B:\n    type: P\n    discriminatorValue: "1.0"\n    properties:\n      b: string\n  T: A | B\n',
        )
        assert shape.validate({'k': '1', 'a': 'x'}) is None
        assert shape.validate({'k': '1.0', 'b': 'x'}) is None
        assert shape.validate({'k': '1', 'b': 'x'}) is not None

    def test_a_boolean_tag_is_not_an_integer_one(self, workspace):
        shape = declared(
            workspace,
            '  P:\n    discriminator: k\n    properties:\n      k: boolean\n'
            '  A:\n    type: P\n    discriminatorValue: true\n    properties:\n      a?: string\n'
            '  B:\n    type: P\n    discriminatorValue: false\n    properties:\n      b?: string\n  T: A | B\n',
        )
        assert shape.validate({'k': True, 'a': 'x'}) is None
        # `1` is not `true`, and reaches the property's own type check.
        assert shape.validate({'k': 1, 'a': 'x'}) is not None

    def test_a_null_tag_falls_back_to_the_linear_scan(self, workspace):
        # Not 'unknown discriminator value: null'. The property's own type says
        # what a tag may be, and it reports that better.
        error = declared(workspace, TAGGED).validate({'kind': None, 'meows': True})
        assert error is not None
        assert 'unknown discriminator value' not in messages(error)
        assert 'value matches no member of the union' in messages(error)

    def test_a_non_scalar_tag_falls_back_to_the_linear_scan(self, workspace):
        error = declared(workspace, TAGGED).validate({'kind': {'a': 1}, 'meows': True})
        assert error is not None
        assert 'unknown discriminator value' not in messages(error)
        assert 'value matches no member of the union' in messages(error)

    def test_a_clone_gets_its_own_table_when_it_is_unwrapped(self, workspace):
        # The table holds the *members*, so a clone carrying the original's would
        # dispatch into the original's graph. A clone is not unwrapped and has no
        # table until P9 runs over it — the path `_ensure_unwrapped` takes for
        # `validate=True` without `unwrap=True`.
        from fastraml.types.complex_ import UnionShape
        from fastraml.types.unwrap import finish_unwrap, unwrap_shape

        raml, shape = declared_in(workspace, TAGGED)
        assert isinstance(shape.shape, UnionShape)
        table = shape.shape.dispatch()
        assert table is not None

        clone = shape.clone_detached()
        assert isinstance(clone.shape, UnionShape)
        assert clone.shape.dispatch() is None, 'a clone must not inherit the table'

        copy = unwrap_shape(raml, clone)
        finish_unwrap(raml, roots=[copy])
        assert isinstance(copy.shape, UnionShape)
        cloned = copy.shape.dispatch()
        assert cloned is not None
        assert cloned.members.keys() == table.members.keys()
        for name, member in cloned.members.items():
            assert member is not table.members[name]

    def test_validating_an_unflattened_shape_is_refused(self, workspace):
        # docs/02 § 4, invariant I12. Without unwrap a child shows only
        # what its own declaration wrote, so it accepts a value missing the
        # property its parent made required — silently, which is the hazard.
        root = workspace({'api.raml': API + 'types:\n' + TAGGED})
        raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=False))
        with pytest.raises(AssertionError, match='unwrapped shape'):
            raml.types_in(raml.location)['Cat'].validate({'meows': True})


class TestRecursive:
    def test_a_cyclic_type_validates_finite_data(self, workspace):
        # No visited set is needed: the data is finite even though the type
        # is not.
        shape = declared(workspace, '  T:\n    properties:\n      name: string\n      next?: T\n')
        assert shape.validate({'name': 'a', 'next': {'name': 'b'}}) is None
        assert shape.validate({'name': 'a', 'next': {'name': 1}}) is not None


class TestEnumFirst:
    """docs/10 § 5: a non-empty `enum` is the whole check."""

    def test_membership_decides(self, workspace):
        shape = declared(workspace, '  T:\n    type: string\n    enum: [red, green]\n')
        assert shape.validate('red') is None
        assert shape.validate('blue') is not None

    def test_facets_are_not_re_run(self, workspace):
        # The members were validated against the facets by `check()`, so a
        # member that satisfies the enum satisfies the shape by construction.
        shape = declared(workspace, '  T:\n    type: string\n    minLength: 1\n    enum: [ok]\n')
        assert shape.validate('ok') is None


class TestExamplesAndDefaults:
    def test_a_bad_example_is_reported(self, workspace):
        error = parse_validating(workspace, '  T:\n    type: integer\n    example: notanumber\n')
        assert error is not None
        assert 'invalid example' in messages(error)

    def test_strict_false_suppresses_it(self, workspace):
        assert (
            parse_validating(
                workspace,
                '  T:\n    type: integer\n    example:\n      value: notanumber\n      strict: false\n',
            )
            is None
        )

    def test_every_named_example_is_checked(self, workspace):
        error = parse_validating(
            workspace,
            '  T:\n    type: integer\n    examples:\n      good: 1\n      bad: notanumber\n',
        )
        assert error is not None
        named = {trace.info.get('example') for chain in error.chains() for trace in chain if trace.info}
        assert 'bad' in named

    def test_a_bad_default_is_reported(self, workspace):
        error = parse_validating(workspace, '  T:\n    type: integer\n    default: notanumber\n')
        assert error is not None
        assert 'invalid default' in messages(error)

    def test_a_default_has_no_strict_escape(self, workspace):
        # An unusable default is always a defect; an example may deliberately
        # show a malformed payload.
        error = parse_validating(
            workspace,
            '  T:\n    type: integer\n    default: notanumber\n    example:\n      value: 1\n      strict: false\n',
        )
        assert error is not None
        assert 'invalid default' in messages(error)

    def test_an_example_nested_in_a_property_is_checked(self, workspace):
        error = parse_validating(
            workspace,
            '  T:\n    properties:\n      a:\n        type: integer\n        example: notanumber\n',
        )
        assert error is not None
        assert 'invalid example' in messages(error)


class TestCustomFacets:
    """docs/10 § 4."""

    def test_a_typo_becomes_an_unknown_facet(self, workspace):
        # The rule that earns its keep: `maxLenght` decoded as a custom facet
        # *value* because nothing before P10 could tell it from a real one.
        error = parse_validating(workspace, '  T:\n    type: string\n    maxLenght: 5\n')
        assert error is not None
        assert 'unknown facet' in messages(error)

    def test_a_declared_facet_is_validated_against_its_declaration(self, workspace):
        error = parse_validating(
            workspace,
            '  P:\n    type: string\n    facets:\n      extra: integer\n  T:\n    type: P\n    extra: notanumber\n',
        )
        assert error is not None
        assert 'invalid custom facet value' in messages(error)

    def test_a_valid_facet_value_passes(self, workspace):
        assert (
            parse_validating(
                workspace,
                '  P:\n    type: string\n    facets:\n      extra: integer\n  T:\n    type: P\n    extra: 5\n',
            )
            is None
        )

    def test_a_required_facet_must_be_supplied_by_a_subtype(self, workspace):
        error = parse_validating(
            workspace, '  P:\n    type: string\n    facets:\n      extra: integer\n  T:\n    type: P\n'
        )
        assert error is not None
        assert 'required custom facet is missing' in messages(error)

    def test_an_optional_facet_need_not_be(self, workspace):
        assert (
            parse_validating(
                workspace, '  P:\n    type: string\n    facets:\n      extra?: integer\n  T:\n    type: P\n'
            )
            is None
        )

    def test_the_declaring_type_need_not_satisfy_its_own_facet(self, workspace):
        # A `facets:` block declares what *subtypes* must supply. Measured
        # against go-raml, which walks the chain from the first parent too.
        assert parse_validating(workspace, '  P:\n    type: string\n    facets:\n      extra: integer\n') is None

    def test_the_declaring_type_may_not_supply_its_own_facet(self, workspace):
        # The other half of the same rule, and the surprising one.
        error = parse_validating(workspace, '  P:\n    type: string\n    facets:\n      extra: integer\n    extra: 5\n')
        assert error is not None
        assert 'unknown facet' in messages(error)

    def test_a_facet_is_inherited_down_a_chain(self, workspace):
        assert (
            parse_validating(
                workspace,
                '  A:\n    type: string\n    facets:\n      extra: integer\n'
                '  B:\n    type: A\n    extra: 5\n'
                '  C:\n    type: B\n',
            )
            is None
        )

    def test_a_recursive_subtype_still_satisfies_its_parents_facet(self, workspace):
        """A recursion marker is a stop, and was being read as a declaration.

        P9 builds one by cloning the cycle's head and clearing `inherits`, so
        the clone keeps the head's `custom_facets` with nothing left to declare
        them. Validated as though it were a type of its own, every facet on it
        came back `unknown facet` — a valid document rejected, and rejected
        once per path that reached the cycle.

        The facet has to be on the *parent*: the rule walks from `inherits[0]`,
        so a type declaring its own facet would pass this by the route § 4
        already covers.
        """
        error = parse_validating(
            workspace,
            '  P:\n    type: object\n    facets:\n      extra: integer\n'
            '  T:\n    type: P\n    extra: 5\n    properties:\n      next?: T\n',
        )
        assert error is None, messages(error)

    def test_a_facet_on_a_second_parent_is_not_seen(self, workspace):
        """The `inherits[0]`-only limitation, pinned so the fix is visible.

        docs/10 § 4 and docs/15 § 2 record it. If this test starts
        failing because the walk grew a visited set, that is the fix landing —
        update the test, do not restore the behaviour.
        """
        assert (
            parse_validating(
                workspace,
                '  A:\n    type: object\n  B:\n    type: object\n    facets:\n      extra: integer\n  C: [A, B]\n',
            )
            is None
        )


class TestUnionFacetsAreDistributed:
    """A facet beside `type: A | B` constrains each expanded branch.

    Spec § Union Type. This class pinned the *gap* until the distribution landed
    — `test_and_is_not_applied` asserted that `maximum: 2` did nothing to an
    `example: 99999`, so that the silence could not go unnoticed. It is now the
    first case below, inverted.
    """

    def test_a_facet_on_a_union_is_accepted(self, workspace):
        assert parse_validating(workspace, '  U: integer | number\n  T:\n    type: U\n    maximum: 2\n') is None

    def test_and_is_applied_to_every_member(self, workspace):
        error = parse_validating(
            workspace, '  U: integer | number\n  T:\n    type: U\n    maximum: 2\n    example: 99999\n'
        )
        assert error is not None
        assert 'invalid example' in messages(error)

    def test_a_conforming_example_still_passes(self, workspace):
        assert (
            parse_validating(workspace, '  U: integer | number\n  T:\n    type: U\n    maximum: 2\n    example: 1\n')
            is None
        )

    def test_a_facet_no_member_supports_is_an_unknown_facet(self, workspace):
        # `minimum` is not a facet of `string`, and nothing declares it as a
        # custom one — so distributing it reports where it landed.
        error = parse_validating(workspace, '  U: integer | string\n  T:\n    type: U\n    minimum: 1\n')
        assert error is not None
        assert 'unknown facet' in messages(error)

    def test_a_member_may_declare_it_as_a_custom_facet(self, workspace):
        body = (
            '  S:\n    type: string\n    facets:\n      minimum: number\n  T:\n    type: integer | S\n    minimum: 1\n'
        )
        assert parse_validating(workspace, body) is None

    def test_conflicting_bounds_are_caught_on_the_members(self, workspace):
        error = parse_validating(
            workspace, '  U: integer | number\n  T:\n    type: U\n    maximum: 1\n    minimum: 2\n'
        )
        assert error is not None
        assert 'minimum exceeds maximum' in messages(error)

    def test_the_declared_union_is_not_mutated(self, workspace):
        """The corruption this design exists to avoid.

        The "both unions" branch of the merge adopts the parent's member objects
        by reference, so decoding a facet in place would narrow `U` itself — and
        with it every other subtype of `U`.
        """
        root = workspace(
            {
                'api.raml': API
                + 'types:\n  U: integer | number\n'
                + '  Narrow:\n    type: U\n    maximum: 2\n'
                + '  Wide:\n    type: U\n    example: 99999\n'
            }
        )
        assert parse_from_path(root / 'api.raml', ParseOptions(validate=True, unwrap=True)) is not None


class TestUnionDeclarationFacetsAreDistributed:
    """`properties:` and `items:` beside `type: A | B` reach each member too.

    docs/07 § 5. They hold declarations, so they are built with the union's
    other declaration facets and each member takes its own copy; handed to a
    member's `decode_facets` as YAML they were filed as unknown custom facets.
    """

    OBJECTS = '  A:\n    properties:\n      a: string\n  B:\n    properties:\n      b: string\n'

    def test_properties_beside_a_union_of_objects_are_accepted(self, workspace):
        body = self.OBJECTS + '  T:\n    type: A | B\n    properties:\n      c: integer\n    example: {a: x, c: 1}\n'
        assert parse_validating(workspace, body) is None

    def test_and_constrain_every_member(self, workspace):
        body = self.OBJECTS + '  T:\n    type: A | B\n    properties:\n      c: integer\n    example: {a: x, c: no}\n'
        error = parse_validating(workspace, body)
        assert error is not None
        paths = {trace.info.get('path') for chain in error.chains() for trace in chain if trace.info}
        assert '$.c' in paths

    def test_items_beside_a_union_of_arrays_constrain_every_member(self, workspace):
        body = (
            '  A:\n    type: array\n    items: string\n  B:\n    type: array\n    items: string\n'
            '  T:\n    type: A | B\n    items:\n      maxLength: 3\n    example: [abcd]\n'
        )
        error = parse_validating(workspace, body)
        assert error is not None
        assert 'invalid example' in messages(error)

    def test_a_member_that_takes_no_properties_reports_an_unknown_facet(self, workspace):
        body = '  A:\n    properties:\n      a: string\n  T:\n    type: A | string\n    properties:\n      c: integer\n'
        error = parse_validating(workspace, body)
        assert error is not None
        assert 'unknown facet' in messages(error)

    def test_a_name_in_a_distributed_property_resolves(self, workspace):
        # Built with the union's other declarations, so P7 binds `N` as usual.
        body = self.OBJECTS + '  T:\n    type: A | B\n    properties:\n      c: N\n    example: {b: x, c: -1}\n'
        error = parse_validating(workspace, body + '  N:\n    type: integer\n    minimum: 0\n')
        assert error is not None
        assert 'invalid example' in messages(error)

    def test_a_nested_union_passes_the_declarations_on(self, workspace):
        body = (
            self.OBJECTS
            + '  C:\n    properties:\n      x: string\n'
            + '  T:\n    type: A | (B | C)\n    properties:\n      c: integer\n    example: {x: y, c: no}\n'
        )
        error = parse_validating(workspace, body)
        assert error is not None
        assert 'invalid example' in messages(error)

    def test_members_take_separate_copies(self, workspace):
        """Each member's merge narrows its copy in place, so a shared copy would
        let one member's parent constrain the other's."""
        _raml, union = declared_in(
            workspace, self.OBJECTS + '  T:\n    type: A | B\n    properties:\n      c: integer\n'
        )
        first, second = (member.shape.properties['c'].base for member in union.shape.any_of)
        assert first is not second
        assert first.id != second.id


class TestPublicSurface:
    """docs/13 § 3: `validate` returns, it does not raise."""

    def test_success_returns_none(self, workspace):
        assert declared(workspace, '  T: string\n').validate('x') is None

    def test_failure_returns_the_error(self, workspace):
        error = declared(workspace, '  T: string\n').validate(1)
        assert isinstance(error, RamlError)

    def test_validate_or_raise_raises(self, workspace):
        with pytest.raises(RamlError):
            declared(workspace, '  T: string\n').validate_or_raise(1)

    def test_the_path_starts_at_the_root(self, workspace):
        error = declared(workspace, '  T:\n    properties:\n      a: integer\n').validate({'a': 'x'})
        paths = {trace.info.get('path') for chain in error.chains() for trace in chain if trace.info}
        assert '$.a' in paths


class TestPrivateUnwrap:
    """`validate=True` without `unwrap=True` must not flatten the caller's model."""

    def test_the_declared_model_keeps_its_inherits(self, workspace):
        root = workspace(
            {
                'api.raml': API + 'types:\n  P:\n    properties:\n      a: string\n  T:\n    type: P\n',
            }
        )
        raml = parse_from_path(root / 'api.raml', ParseOptions(validate=True))
        child = raml.types_in(raml.location)['T']
        assert not raml.is_unwrapped
        assert [parent.name for parent in child.inherits] == ['P']
        # Flattening would have copied `a` onto the child.
        assert list(child.shape.properties or {}) == []

    def test_validation_still_sees_the_inherited_shape(self, workspace):
        # The copy is what gets checked, so an inherited facet still bites.
        root = workspace(
            {
                'api.raml': API + 'types:\n'
                '  P:\n    type: integer\n    maximum: 5\n'
                '  T:\n    type: P\n    example: 99\n',
            }
        )
        with pytest.raises(RamlError):
            parse_from_path(root / 'api.raml', ParseOptions(validate=True))


# -- property-based: inheritance narrows ----------------------------------

#: `(parent facets, child facets, values)`. The child narrows the parent in each
#: pair, which is what makes the law meaningful rather than vacuous.
NARROWING = [
    ('type: integer\n    maximum: 100', 'maximum: 10', [1, 5, 10, 11, 500, 'x', True]),
    ('type: string\n    maxLength: 10', 'maxLength: 3', ['', 'ab', 'abcd', 'a' * 20, 7]),
    ('type: string\n    minLength: 1', 'minLength: 4', ['', 'a', 'abcd', 'abcdef']),
    ('type: number\n    minimum: 0', 'minimum: 10', [-1, 0, 9.5, 10, 100.25]),
    ('type: integer', 'enum: [1, 2, 3]', [1, 2, 3, 4, 'x']),
]


@settings(max_examples=len(NARROWING), deadline=None)
@given(case=st.sampled_from(NARROWING))
def test_a_value_valid_for_a_child_is_valid_for_its_parent(tmp_path_factory, case):
    """Inheritance narrows (docs/07 § 4).

    If `child` inherits `parent`, every value the child accepts the parent must
    accept too. A per-kind rule that widened instead of narrowing would satisfy
    its own unit test and break this.
    """
    from tests.unit.conftest import write_files

    parent_facets, child_facets, values = case
    body = f'  P:\n    {parent_facets}\n  C:\n    type: P\n    {child_facets}\n'
    root = write_files(tmp_path_factory.mktemp('narrow'), {'api.raml': API + 'types:\n' + body})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    types = raml.types_in(raml.location)
    parent, child = types['P'], types['C']

    accepted = [value for value in values if child.validate(value) is None]
    assert accepted, 'the child accepted nothing; the law would be vacuous'
    for value in accepted:
        assert parent.validate(value) is None, f'{value!r} passes the child but not the parent'
