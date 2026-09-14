from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest
from fastraml import BaseShape, ParseOptions, parse_from_string
from hypothesis import given, settings
from hypothesis import strategies as st

from raml_mock import GenerationOptions
from raml_mock.generate import generate

BASE_DIR = pathlib.Path.cwd()


def shape(declaration: str) -> BaseShape:
    document = parse_from_string(
        '#%RAML 1.0\ntitle: Generated\n/x:\n  get:\n    responses:\n      200:\n        body:\n'
        f'          application/json:\n{declaration}',
        file_name='generated.raml',
        base_dir=BASE_DIR,
        options=ParseOptions(unwrap=True, validate=True),
    )
    result = document.endpoints['/x'].operations['get'].responses['200'].bodies['application/json'].shape
    assert result is not None
    return result


@settings(max_examples=30)
@given(low=st.integers(-1000, 1000), width=st.integers(0, 100), step=st.integers(1, 20))
def test_generated_integers_satisfy_bounds_and_multiples(low: int, width: int, step: int) -> None:
    lower = low * step
    upper = lower + width * step
    base = shape(
        f'            type: integer\n            minimum: {lower}\n'
        f'            maximum: {upper}\n            multipleOf: {step}\n'
    )
    value = generate(base, options=GenerationOptions(seed='property'), key='integer')
    assert base.validate(value) is None


@settings(max_examples=30)
@given(low=st.integers(-100, 100), width=st.integers(0, 20), step=st.integers(1, 20))
def test_generated_decimals_satisfy_bounds_and_multiples(low: int, width: int, step: int) -> None:
    increment = Decimal(step) / 10
    lower = Decimal(low) * increment
    upper = lower + Decimal(width) * increment
    base = shape(
        f'            type: number\n            minimum: {lower}\n'
        f'            maximum: {upper}\n            multipleOf: {increment}\n'
    )
    value = generate(base, options=GenerationOptions(seed=17), key='number')
    assert base.validate(value) is None


@settings(max_examples=25)
@given(size=st.integers(1, 12))
def test_generated_unique_arrays_are_valid(size: int) -> None:
    base = shape(
        f'            type: array\n            minItems: {size}\n'
        '            uniqueItems: true\n            items: integer\n'
    )
    value = generate(base, options=GenerationOptions(seed='array'), key='array')
    assert base.validate(value) is None


@settings(max_examples=20)
@given(size=st.integers(1, 10), width=st.integers(10, 30))
def test_seeded_unique_arrays_use_the_available_bounded_domain(size: int, width: int) -> None:
    base = shape(
        f'            type: array\n            minItems: {size}\n'
        f'            uniqueItems: true\n            items:\n              type: integer\n'
        f'              minimum: 0\n              maximum: {width}\n'
    )
    value = generate(base, options=GenerationOptions(seed='bounded'), key='array')
    assert base.validate(value) is None


@settings(max_examples=20)
@given(size=st.integers(1, 8))
def test_generated_pattern_properties_are_typed(size: int) -> None:
    base = shape(
        f'            type: object\n            minProperties: {size}\n'
        '            properties:\n              /^x-/:\n                type: integer\n                minimum: 5\n'
    )
    value = generate(base, options=GenerationOptions(seed='object'), key='object')
    assert base.validate(value) is None


@settings(max_examples=20, deadline=None)
@given(size=st.integers(1, 8), collide=st.integers(1, 8))
def test_min_properties_terminates_when_a_declared_name_collides(size: int, collide: int) -> None:
    # The synthesized filler is named `propertyN`. A *declared* property of the
    # same name used to be overwritten instead of added, so the count never
    # reached `minProperties` and generation span forever inside the handler.
    base = shape(
        f'            type: object\n            minProperties: {size}\n'
        f'            properties:\n              property{collide}: string\n'
    )
    value = generate(base, options=GenerationOptions(seed='collide'), key='object')
    assert isinstance(value, dict)
    assert len(value) >= size
    assert base.validate(value) is None


def test_json_distinguishes_booleans_from_numbers_for_unique_items() -> None:
    base = shape(
        '            type: array\n            minItems: 2\n            uniqueItems: true\n'
        '            items:\n              type: any\n              enum: [true, 1]\n'
    )
    value = generate(base)
    assert value == [True, 1]
    assert base.validate(value) is None


def test_unique_array_generation_is_not_capped_at_sixty_four_items() -> None:
    base = shape(
        '            type: array\n            minItems: 65\n            uniqueItems: true\n            items: integer\n'
    )
    value = generate(base)
    assert isinstance(value, list)
    assert len(value) == 65
    assert base.validate(value) is None


@pytest.mark.parametrize('kind', ['integer', 'number'])
def test_negative_multiple_of_generates_a_valid_value(kind: str) -> None:
    base = shape(
        f'            type: {kind}\n            minimum: 1\n            maximum: 3\n            multipleOf: -2\n'
    )
    value = generate(base)
    assert value == 2
    assert base.validate(value) is None


def test_literal_string_pattern_is_generated() -> None:
    base = shape('            type: string\n            pattern: ^b$\n')
    assert generate(base) == 'b'


def test_returned_examples_do_not_alias_the_parse_model() -> None:
    base = shape('            type: object\n            example: {items: [one]}\n')
    first = generate(base)
    assert isinstance(first, dict)
    first['items'] = []
    assert generate(base) == {'items': ['one']}
