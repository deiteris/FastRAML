"""A value for a shape — docs/16 § 8.1.

The policy is pinned rule by rule: the shape's own declared values first, never
a supertype's; composition from properties and items; synthesis only when it
is allowed. The property tests at the end pin that whatever is synthesized
validates, across the facet ranges a document can write.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fastraml import ParseOptions, parse_from_string
from fastraml.views.samples import SampleError, SampleOptions, declared_values, named_example, sample

BASE_DIR = pathlib.Path.cwd()

BOOK = (
    'types:\n'
    '  Book:\n'
    '    properties:\n'
    '      title:\n'
    '        type: string\n'
    '        example: Dune\n'
    '      year:\n'
    '        type: integer\n'
    '        example: 1965\n'
    '      note?:\n'
    '        type: string\n'
    '    example: {title: Solaris, year: 1961}\n'
)


def body(declaration: str, types: str = '') -> object:
    """The unwrapped shape of one response body, declared as *declaration*."""
    document = parse_from_string(
        f'#%RAML 1.0\ntitle: T\n{types}/x:\n  get:\n    responses:\n      200:\n        body:\n'
        f'          application/json:\n{declaration}',
        file_name='api.raml',
        base_dir=BASE_DIR,
        options=ParseOptions(unwrap=True, validate=True),
    )
    return document.endpoints['/x'].operations['get'].responses['200'].bodies['application/json'].shape


def compose(**options: object) -> SampleOptions:
    return SampleOptions(synthesize=False, **options)  # type: ignore[arg-type]


class TestDeclared:
    def test_the_shapes_own_example_comes_first(self):
        assert sample(body('            type: string\n            example: hello\n')) == 'hello'

    def test_a_default_is_used_when_there_is_no_example(self):
        assert sample(body('            type: integer\n            default: 7\n')) == 7

    def test_an_enum_member_is_used_before_synthesis(self):
        assert sample(body('            type: string\n            enum: [red, green]\n')) == 'red'

    def test_a_non_strict_example_is_never_chosen(self):
        shape = body(
            '            type: integer\n            examples:\n'
            '              loose:\n                value: 5\n                strict: false\n'
            '              kept: 6\n'
        )
        assert declared_values(shape) == [6]
        assert sample(shape) == 6

    def test_a_non_strict_example_can_still_be_named(self):
        shape = body(
            '            type: integer\n            examples:\n              loose:\n                value: 5\n                strict: false\n'
        )
        assert named_example(shape, 'loose') == 5

    def test_a_named_example_must_be_the_shapes_own(self):
        shape = body(
            '            type: Book\n            description: a subtype\n',
            BOOK.replace(
                '    example: {title: Solaris, year: 1961}\n',
                '    examples:\n      solaris: {title: Solaris, year: 1961}\n',
            ),
        )
        with pytest.raises(SampleError):
            named_example(shape, 'solaris')


class TestNoSupertypeExample:
    """A subtype may narrow or add a required property, so its parent's example is never tried."""

    def test_a_subtype_composes_from_its_properties_instead(self):
        shape = body('            type: Book\n            description: a subtype\n', BOOK)
        assert declared_values(shape) == []
        assert sample(shape) == {'title': 'Dune', 'year': 1965}

    def test_an_alias_is_the_type_and_carries_its_example(self):
        # `application/json: Book` names Book itself (docs/07 § 3); nothing is borrowed.
        document = parse_from_string(
            f'#%RAML 1.0\ntitle: T\n{BOOK}/x:\n  get:\n    responses:\n      200:\n        body:\n'
            '          application/json: Book\n',
            file_name='api.raml',
            base_dir=BASE_DIR,
            options=ParseOptions(unwrap=True, validate=True),
        )
        shape = document.endpoints['/x'].operations['get'].responses['200'].bodies['application/json'].shape
        assert sample(shape) == {'title': 'Solaris', 'year': 1961}


class TestComposeOnly:
    """`synthesize=False`: a value built from declared data alone."""

    def test_an_object_is_its_properties_examples(self):
        shape = body('            type: Book\n            description: a subtype\n', BOOK)
        assert sample(shape, options=compose()) == {'title': 'Dune', 'year': 1965}

    def test_an_optional_property_with_an_example_is_included(self):
        types = BOOK.replace(
            '      note?:\n        type: string\n', '      note?:\n        type: string\n        example: classic\n'
        )
        shape = body('            type: Book\n            description: a subtype\n', types)
        assert sample(shape, options=compose())['note'] == 'classic'

    def test_an_optional_property_built_from_nothing_is_left_out(self):
        shape = body(
            '            properties:\n              name:\n                type: string\n                example: x\n'
            '              meta?:\n                properties:\n                  tag?: string\n'
        )
        assert sample(shape, options=compose()) == {'name': 'x'}

    def test_a_required_leaf_without_a_declared_value_fails(self):
        shape = body(
            '            properties:\n              name:\n                type: string\n                example: x\n              id: integer\n'
        )
        with pytest.raises(SampleError):
            sample(shape, options=compose())
        assert sample(shape) == {'name': 'x', 'id': 0}

    def test_a_value_with_nothing_declared_in_it_fails(self):
        shape = body('            properties:\n              meta?: string\n')
        with pytest.raises(SampleError):
            sample(shape, options=compose())
        assert sample(shape) == {}

    def test_an_array_is_its_items_examples(self):
        shape = body(
            '            type: array\n            items:\n              type: integer\n              examples:\n                a: 1\n                b: 2\n'
        )
        assert sample(shape, options=compose()) == [1, 2]


class TestComposition:
    def test_a_union_takes_its_first_member_that_yields_a_value(self):
        shape = body('            type: nil | integer\n')
        assert sample(shape) is None

    def test_a_required_recursive_value_without_an_example_fails(self):
        types = 'types:\n  Node:\n    properties:\n      next: Node\n'
        with pytest.raises(SampleError):
            sample(body('            type: Node\n            description: d\n', types))

    def test_an_optional_recursion_stops(self):
        types = 'types:\n  Node:\n    properties:\n      name:\n        type: string\n        example: n\n      next?: Node\n'
        assert sample(body('            type: Node\n            description: d\n', types)) == {'name': 'n'}

    def test_a_returned_value_does_not_share_the_models_containers(self):
        shape = body('            type: object\n            example: {items: [one]}\n')
        first = sample(shape)
        first['items'].append('two')
        assert sample(shape) == {'items': ['one']}

    def test_a_seed_picks_deterministically_among_examples(self):
        shape = body(
            '            type: integer\n            examples:\n              a: 1\n              b: 2\n              c: 3\n'
        )
        options = SampleOptions(seed='s')
        assert sample(shape, options=options, key='k') == sample(shape, options=options, key='k')
        assert {sample(shape, key=str(key)) for key in range(100)} == {1}, 'no seed takes the first'
        assert {sample(shape, options=options, key=str(key)) for key in range(100)} == {1, 2, 3}

    def test_a_seed_decides_optional_properties_reproducibly(self):
        properties = ''.join(f'              p{index}?: string\n' for index in range(12))
        shape = body(f'            properties:\n{properties}')
        options = SampleOptions(seed=3, optional_probability=0.5)
        first = sample(shape, options=options, key='k')
        assert sample(shape, options=options, key='k') == first
        assert 0 < len(first) < 12


class TestOptions:
    def test_a_negative_collection_size_is_refused(self):
        with pytest.raises(ValueError, match='collection_size'):
            SampleOptions(collection_size=-1)

    def test_a_probability_outside_zero_to_one_is_refused(self):
        with pytest.raises(ValueError, match='optional_probability'):
            SampleOptions(optional_probability=1.5)


# -- synthesis properties -----------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(low=st.integers(-1000, 1000), width=st.integers(0, 100), step=st.integers(1, 20))
def test_synthesized_integers_satisfy_bounds_and_multiples(low, width, step):
    lower = low * step
    upper = lower + width * step
    base = body(
        f'            type: integer\n            minimum: {lower}\n            maximum: {upper}\n            multipleOf: {step}\n'
    )
    assert base.validate(sample(base, options=SampleOptions(seed='property'), key='integer')) is None


@settings(max_examples=30, deadline=None)
@given(low=st.integers(-100, 100), width=st.integers(0, 20), step=st.integers(1, 20))
def test_synthesized_decimals_satisfy_bounds_and_multiples(low, width, step):
    increment = Decimal(step) / 10
    lower = Decimal(low) * increment
    upper = lower + Decimal(width) * increment
    base = body(
        f'            type: number\n            minimum: {lower}\n            maximum: {upper}\n            multipleOf: {increment}\n'
    )
    assert base.validate(sample(base, options=SampleOptions(seed=17), key='number')) is None


@settings(max_examples=20, deadline=None)
@given(size=st.integers(1, 10), width=st.integers(10, 30))
def test_seeded_unique_arrays_use_the_available_bounded_domain(size, width):
    base = body(
        f'            type: array\n            minItems: {size}\n            uniqueItems: true\n'
        f'            items:\n              type: integer\n              minimum: 0\n              maximum: {width}\n'
    )
    assert base.validate(sample(base, options=SampleOptions(seed='bounded'), key='array')) is None


@settings(max_examples=20, deadline=None)
@given(size=st.integers(1, 8))
def test_synthesized_pattern_properties_are_typed(size):
    base = body(
        f'            type: object\n            minProperties: {size}\n'
        '            properties:\n              /^x-/:\n                type: integer\n                minimum: 5\n'
    )
    assert base.validate(sample(base, options=SampleOptions(seed='object'), key='object')) is None


@settings(max_examples=20, deadline=None)
@given(size=st.integers(1, 8), collide=st.integers(1, 8))
def test_min_properties_terminates_when_a_declared_name_collides(size, collide):
    # The synthesized filler is named `propertyN`. A declared property of the
    # same name used to be overwritten instead of added, so the count never
    # reached `minProperties` and sampling never returned.
    base = body(
        f'            type: object\n            minProperties: {size}\n            properties:\n              property{collide}: string\n'
    )
    value = sample(base, options=SampleOptions(seed='collide'), key='object')
    assert len(value) >= size


def test_json_distinguishes_booleans_from_numbers_for_unique_items():
    base = body(
        '            type: array\n            minItems: 2\n            uniqueItems: true\n'
        '            items:\n              type: any\n              enum: [true, 1]\n'
    )
    assert sample(base) == [True, 1]


def test_a_unique_array_is_not_capped_at_sixty_four_items():
    base = body(
        '            type: array\n            minItems: 65\n            uniqueItems: true\n            items: integer\n'
    )
    assert len(sample(base)) == 65


@pytest.mark.parametrize('kind', ['integer', 'number'])
def test_a_negative_multiple_of_samples_a_valid_value(kind):
    base = body(
        f'            type: {kind}\n            minimum: 1\n            maximum: 3\n            multipleOf: -2\n'
    )
    assert sample(base) == 2


def test_a_literal_string_pattern_is_synthesized():
    assert sample(body('            type: string\n            pattern: ^b$\n')) == 'b'


def test_a_decimal_bound_is_sampled_exactly():
    # docs/10 § 5: never through `float`; 0.1 stays Decimal('0.1').
    value = sample(body('            type: number\n            minimum: 0.1\n            maximum: 0.1\n'))
    assert value == Decimal('0.1')
