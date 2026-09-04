"""`validate(value)` — does *data* conform to a declaration?

docs/10-validation.md sections 3 to 5. One test per row of section 5's table,
plus the three hazards doc 14 section 3 names by hand: `bool` must not pass as
`integer`, `multipleOf: 1.1` must be exact, and `uniqueItems` must behave the
same either side of the n=20 strategy switch.

`tests/unit/test_check.py` is the other half — declaration consistency.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pyraml import ParseOptions, RamlError, parse_from_path
from pyraml.types.values import same_value, unique_items

API = '#%RAML 1.0\ntitle: T\n'


def declared(workspace, body: str, name: str = 'T'):
    """The named type from `types:\n<body>`, parsed and unwrapped."""
    root = workspace({'api.raml': API + 'types:\n' + body})
    raml = parse_from_path(root / 'api.raml', ParseOptions(unwrap=True))
    return raml.types_in(raml.location)[name]


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
    """docs/10 section 5.3: nothing goes through `float`."""

    def test_multiple_of_a_decimal_is_exact(self, workspace):
        # Both sides go through decimal text. `2.2` as an exact binary ratio is
        # 2476979795053773/1125899906842624, which `11/10` does not divide.
        shape = declared(workspace, '  T:\n    type: number\n    multipleOf: 1.1\n')
        assert shape.validate(2.2) is None
        assert shape.validate(2.3) is not None

    def test_a_decoded_float_is_read_as_its_written_decimal(self, workspace):
        from pyraml.types.values import as_fraction

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

    def test_a_pattern_describes_the_whole_value(self, workspace):
        """`pattern:` is a full match, not a search.

        This test asserted the opposite until Phase 8b, on ECMA-262 semantics.
        The TCK decides it: `Annotations/complex-11`'s valid and invalid
        fixtures differ only in `simpleAnnotationValueOnType` versus
        `simpleAnnotation_value_on_type` against `[a-zA-Z0-9]{8,32}`, and an
        unanchored search accepts both — the first sixteen characters match.
        """
        assert declared(workspace, '  T:\n    type: string\n    pattern: b\n').validate('abc') is not None
        assert declared(workspace, '  T:\n    type: string\n    pattern: a.c\n').validate('abc') is None

    def test_an_author_written_anchor_still_works(self, workspace):
        # `^`/`$` are redundant under a full match rather than wrong, and real
        # documents are full of them.
        assert declared(workspace, '  T:\n    type: string\n    pattern: ^abc$\n').validate('abc') is None
        assert declared(workspace, '  T:\n    type: string\n    pattern: ^b\n').validate('abc') is not None

    def test_a_pattern_property_name_is_still_matched_unanchored(self, workspace):
        # The other direction, and the reason the change is not global: a
        # `/regex/` key is matched *against* a property name rather than
        # describing one, and `/^x/` is how they are written (docs/05 § 5.1).
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
    """docs/10 section 5.2 — two strategies, one meaning."""

    def test_the_two_strategies_agree_across_the_switch(self, workspace):
        # 20 is pairwise, 21 hashes. A difference here would be invisible in
        # normal use and wrong in exactly one size range.
        for size in (20, 21):
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


class TestObject:
    def test_a_missing_required_property_fails(self, workspace):
        shape = declared(workspace, '  T:\n    properties:\n      a: string\n      b?: string\n')
        assert shape.validate({'a': 'x'}) is None
        assert shape.validate({'b': 'x'}) is not None

    def test_every_missing_property_is_named_in_one_message(self, workspace):
        # docs/10 section 5.1: one message listing all of them, not one each.
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


class TestRecursive:
    def test_a_cyclic_type_validates_finite_data(self, workspace):
        # No visited set is needed: the data is finite even though the type
        # is not.
        shape = declared(workspace, '  T:\n    properties:\n      name: string\n      next?: T\n')
        assert shape.validate({'name': 'a', 'next': {'name': 'b'}}) is None
        assert shape.validate({'name': 'a', 'next': {'name': 1}}) is not None


class TestEnumFirst:
    """docs/10 section 5: a non-empty `enum` is the whole check."""

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
    """docs/10 section 4."""

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
        # against go-raml, whose `validateShapeFacets` walks from Inherits[0].
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

    def test_a_facet_on_a_second_parent_is_not_seen(self, workspace):
        """The `inherits[0]`-only limitation, pinned so the fix is visible.

        docs/10 section 4 tracks this as a v1.1 item. If this test starts
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


class TestUnionFacetsAreNotEnforced:
    """docs/01 section 3.7 — a known gap, pinned so it cannot go silent."""

    def test_a_facet_on_a_union_is_accepted(self, workspace):
        assert parse_validating(workspace, '  U: integer | number\n  T:\n    type: U\n    maximum: 2\n') is None

    def test_and_is_not_applied(self, workspace):
        # The failure mode this records: a document that looks constrained is
        # not. When the conformant fix lands this test should start failing.
        assert (
            parse_validating(
                workspace, '  U: integer | number\n  T:\n    type: U\n    maximum: 2\n    example: 99999\n'
            )
            is None
        )


class TestPublicSurface:
    """docs/13 section 5: `validate` returns, it does not raise."""

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


# -- property-based (docs/14 section 4, law 7) ---------------------------------

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
    """docs/14 section 4, law 7: inheritance narrows.

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
