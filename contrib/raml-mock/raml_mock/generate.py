from __future__ import annotations

import base64
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING

from pyraml import (
    AnyShape,
    ArrayShape,
    BaseShape,
    BooleanShape,
    DateOnlyShape,
    DateTimeOnlyShape,
    DateTimeShape,
    FileShape,
    IntegerShape,
    NilShape,
    NumberShape,
    ObjectShape,
    RecursiveShape,
    StringShape,
    TimeOnlyShape,
    UnionShape,
)

from raml_mock.errors import MockGenerationError
from raml_mock.shapes import projected_shape, shape_name

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['generate']

_DATE = '2000-01-01'
_TIME = '00:00:00'
_DATETIME = '2000-01-01T00:00:00'
_RFC3339 = '2000-01-01T00:00:00Z'
_RFC2616 = 'Sat, 01 Jan 2000 00:00:00 GMT'
_MISSING = object()
_FIXED_SCALARS: dict[type[object], object] = {
    AnyShape: None,
    NilShape: None,
    DateOnlyShape: _DATE,
    TimeOnlyShape: _TIME,
    DateTimeOnlyShape: _DATETIME,
}


def generate(base: BaseShape, *, example: str | None = None) -> object:
    """Produce a deterministic value and prove it conforms to *base*."""
    if example is not None:
        selected = next(
            (
                candidate.examples.entries()[example]
                for candidate in _lineage(base)
                if candidate.examples is not None and example in candidate.examples.entries()
            ),
            None,
        )
        if selected is None or selected.data is None:
            raise MockGenerationError(f'unknown example {example!r} for {shape_name(base)}')
        value = selected.data.raw
    else:
        value = _generate(base, frozenset(), 0)
    error = base.validate(value)
    if error is not None:
        msg = f'could not generate a valid value for {shape_name(base)}: {error}'
        raise MockGenerationError(msg)
    return value


def _declared(base: BaseShape) -> tuple[bool, object]:
    for candidate in _example_values(base):
        if _valid(base, candidate):
            return True, candidate
    if base.default is not None:
        return True, base.default.raw
    if base.enum:
        return True, base.enum[0].raw
    return False, None


def _example_values(base: BaseShape) -> Iterator[object]:
    if base.example is not None and base.example.data is not None:
        yield base.example.data.raw
    if base.examples is not None:
        for example in base.examples.entries().values():
            if example.data is not None:
                yield example.data.raw


def _lineage(base: BaseShape) -> Iterator[BaseShape]:
    """Yield *base* then its nearest ancestors, once each."""
    pending = [base]
    seen: set[int] = set()
    index = 0
    while index < len(pending):
        candidate = pending[index]
        index += 1
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        yield candidate
        pending.extend(candidate.inherits)


def _ancestor_example(base: BaseShape) -> tuple[bool, object]:
    """Find the nearest parent example that also satisfies *base*."""
    lineage = _lineage(base)
    next(lineage)
    for ancestor in lineage:
        for candidate in _example_values(ancestor):
            if _valid(base, candidate):
                return True, candidate
    return False, None


def _has_example(base: BaseShape) -> bool:
    return any(any(_valid(base, value) for value in _example_values(candidate)) for candidate in _lineage(base))


def _generate(base: BaseShape, active: frozenset[int], variant: int) -> object:
    declared, value = _declared(base)
    if declared:
        return value
    inherited, value = _ancestor_example(base)
    if inherited:
        return value
    if base.id in active:
        raise MockGenerationError(f'required recursive value has no finite example: {shape_name(base)}')
    projected = projected_shape(base)
    if projected is not base:
        return _generate(projected, active, variant)
    concrete = base.shape
    if isinstance(concrete, RecursiveShape):
        raise MockGenerationError(f'required recursive value has no finite example: {shape_name(base)}')

    active = active | {base.id}
    if isinstance(concrete, (ObjectShape, ArrayShape, UnionShape)):
        return _generate_complex(base, concrete, active, variant)
    return _generate_scalar(base, concrete, variant)


def _generate_scalar(base: BaseShape, shape: object | None, variant: int) -> object:
    fixed = _FIXED_SCALARS.get(type(shape), _MISSING)
    if fixed is not _MISSING:
        return fixed
    if isinstance(shape, BooleanShape):
        return bool(variant % 2)
    if isinstance(shape, StringShape):
        return _string(base, shape, variant)
    if isinstance(shape, (IntegerShape, NumberShape)):
        return _numeric(base, shape, variant)
    if isinstance(shape, DateTimeShape):
        return _RFC2616 if shape.format is not None and shape.format.value == 'rfc2616' else _RFC3339
    if isinstance(shape, FileShape):
        size = shape.min_length.value if shape.min_length is not None else 0
        return base64.b64encode(bytes(size)).decode('ascii')
    raise MockGenerationError(f'unsupported shape for generation: {shape_name(base)}')


def _numeric(base: BaseShape, shape: IntegerShape | NumberShape, variant: int) -> int | Decimal:
    if isinstance(shape, IntegerShape):
        return _integer(base, shape, variant)
    return _number(base, shape, variant)


def _generate_complex(
    base: BaseShape,
    shape: ObjectShape | ArrayShape | UnionShape,
    active: frozenset[int],
    variant: int,
) -> object:
    if isinstance(shape, ObjectShape):
        return _object(base, shape, active, variant)
    if isinstance(shape, ArrayShape):
        count = shape.min_items.value if shape.min_items is not None else 0
        if shape.items is None:
            return [None] * count
        if count == 0 and _has_example(shape.items):
            count = 1
        return [_generate(shape.items, active, variant + index) for index in range(count)]
    for member in shape.any_of or ():
        try:
            candidate = _generate(member, active, variant)
        except MockGenerationError:
            continue
        if member.validate(candidate) is None:
            return candidate
    raise MockGenerationError(f'no union member can be generated for {shape_name(base)}')


def _valid(base: BaseShape, value: object) -> bool:
    return base.validate(value) is None


def _string(base: BaseShape, shape: StringShape, variant: int) -> str:
    minimum = shape.min_length.value if shape.min_length is not None else 0
    candidates = [
        f'string{variant}' if variant else 'string',
        f'mock{variant}',
        str(variant),
        '',
        'a' * max(1, minimum),
    ]
    for candidate in candidates:
        adjusted = candidate + 'a' * max(0, minimum - len(candidate))
        if _valid(base, adjusted):
            return adjusted
    pattern = shape.pattern.value.pattern if shape.pattern is not None else None
    raise MockGenerationError(f'cannot synthesize string pattern {pattern!r} for {shape_name(base)}')


def _integer(base: BaseShape, shape: IntegerShape, variant: int) -> int:
    low = shape.minimum.value if shape.minimum is not None else 0
    high = shape.maximum.value if shape.maximum is not None else low + 100
    candidates = [0, low, low + variant, 1, -1, high]
    if shape.multiple_of is not None:
        step = shape.multiple_of.value
        stride = step.numerator
        quotient, remainder = divmod(low, stride)
        candidates.insert(0, (quotient + bool(remainder)) * stride)
    for candidate in candidates:
        if _valid(base, candidate):
            return candidate
    raise MockGenerationError(f'cannot synthesize integer for {shape_name(base)}')


def _number(base: BaseShape, shape: NumberShape, variant: int) -> int | Decimal:
    low = shape.minimum.value if shape.minimum is not None else Fraction(0)
    values = [Fraction(0), low, low + variant, Fraction(1), Fraction(-1)]
    if shape.multiple_of is not None:
        step = shape.multiple_of.value
        quotient = low // step
        values.insert(0, (quotient if quotient * step >= low else quotient + 1) * step)
    for value in values:
        candidate: int | Decimal = value.numerator if value.denominator == 1 else _finite_decimal(value)
        if _valid(base, candidate):
            return candidate
    raise MockGenerationError(f'cannot synthesize number for {shape_name(base)}')


def _finite_decimal(value: Fraction) -> Decimal:
    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise MockGenerationError(f'number has no finite decimal representation: {value}')
    scale = max(twos, fives)
    coefficient = abs(value.numerator) * (5 ** (scale - twos)) * (2 ** (scale - fives))
    digits = tuple(int(digit) for digit in str(coefficient))
    return Decimal((value.numerator < 0, digits, -scale))


def _object(base: BaseShape, shape: ObjectShape, active: frozenset[int], variant: int) -> dict[str, object]:
    value: dict[str, object] = {}
    properties = shape.properties or {}
    for name, prop in properties.items():
        if not prop.required:
            continue
        value[name] = _generate(prop.base, active, variant)
    if shape.discriminator is not None:
        name = shape.discriminator.value
        if shape.discriminator_value is not None:
            value[name] = shape.discriminator_value.raw
        elif name in properties and name not in value:
            value[name] = _generate(properties[name].base, active, variant)
    minimum = shape.min_properties.value if shape.min_properties is not None else 0
    for name, prop in properties.items():
        if len(value) >= minimum:
            break
        if name not in value:
            try:
                value[name] = _generate(prop.base, active, variant)
            except MockGenerationError:
                continue
    if len(value) < minimum and (shape.additional_properties is None or shape.additional_properties.value):
        while len(value) < minimum:
            value[f'property{len(value) + 1}'] = 'string'
    if not _valid(base, value):
        raise MockGenerationError(f'cannot synthesize object for {shape_name(base)}')
    return value
