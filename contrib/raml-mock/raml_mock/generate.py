"""Generate a deterministic value that conforms to an unwrapped shape, preferring examples."""

from __future__ import annotations

import base64
import hashlib
import re
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, NamedTuple, cast

from fastraml import (
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
    projected,
    same_value,
)

from raml_mock.config import GenerationOptions
from raml_mock.errors import MockGenerationError
from raml_mock.shapes import shape_name
from raml_mock.values import detach

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['generate']

_DATE = '2000-01-01'
_TIME = '00:00:00'
_DATETIME = '2000-01-01T00:00:00'
_RFC3339 = '2000-01-01T00:00:00Z'
_RFC2616 = 'Sat, 01 Jan 2000 00:00:00 GMT'
_MISSING = object()
_LITERAL_PREFIX = re.compile(r'^\^?([A-Za-z0-9_-]+)')
_MAX_STALLED_ATTEMPTS = 64
_FIXED_SCALARS: dict[type[object], object] = {
    AnyShape: None,
    NilShape: None,
    DateOnlyShape: _DATE,
    TimeOnlyShape: _TIME,
    DateTimeOnlyShape: _DATETIME,
}


class _Policy(NamedTuple):
    examples: bool = True
    default: bool = True
    generation: GenerationOptions = GenerationOptions()
    key: str = ''


_DEFAULT_POLICY = _Policy()


def generate(
    base: BaseShape,
    *,
    example: str | None = None,
    options: GenerationOptions | None = None,
    key: str = '',
) -> object:
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
        generation = options or GenerationOptions()
        value = _generate(
            base,
            frozenset(),
            _initial_variant(generation, key),
            policy=_Policy(generation=generation, key=key),
        )
    error = base.validate(value)
    if error is not None:
        msg = f'could not generate a valid value for {shape_name(base)}: {error}'
        raise MockGenerationError(msg)
    return detach(value)


def _declared(base: BaseShape, variant: int, policy: _Policy) -> tuple[bool, object]:
    examples = _examples(base, ancestors=False) if policy.examples else []
    if examples:
        return True, examples[variant % len(examples)]
    if policy.default and base.default is not None:
        return True, base.default.raw
    if base.enum:
        return True, base.enum[variant % len(base.enum)].raw
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


def _examples(base: BaseShape, *, ancestors: bool = True, only_ancestors: bool = False) -> list[object]:
    """Examples along *base*'s lineage that *base* itself accepts.

    An ancestor's example is a mock input candidate, not an inherited RAML
    facet, so it counts only where it satisfies the effective shape. The three
    callers differ just in how much of the lineage they look at: the shape
    alone, the shape and its ancestors, or the ancestors alone.
    """
    lineage = _lineage(base)
    if only_ancestors:
        next(lineage, None)
    elif not ancestors:
        lineage = iter([base])
    return [candidate for shape in lineage for candidate in _example_values(shape) if _valid(base, candidate)]


def _generate(
    base: BaseShape,
    active: frozenset[int],
    variant: int,
    *,
    policy: _Policy = _DEFAULT_POLICY,
) -> object:
    declared, value = _declared(base, variant, policy)
    if declared:
        return value
    if policy.examples:
        inherited = _examples(base, only_ancestors=True)
        if inherited:
            return inherited[variant % len(inherited)]
    if base.id in active:
        raise MockGenerationError(f'required recursive value has no finite example: {shape_name(base)}')
    view = projected(base)
    if view is not base:
        return _generate(view, active, variant, policy=policy)
    concrete = base.shape
    if isinstance(concrete, RecursiveShape):
        raise MockGenerationError(f'required recursive value has no finite example: {shape_name(base)}')

    active = active | {base.id}
    if isinstance(concrete, (ObjectShape, ArrayShape, UnionShape)):
        return _generate_complex(
            base,
            concrete,
            active,
            variant,
            policy=policy,
        )
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
    policy: _Policy,
) -> object:
    if isinstance(shape, ObjectShape):
        return _object(base, shape, active, variant, policy)
    if isinstance(shape, ArrayShape):
        return _array(base, shape, active, variant, policy)
    for member in shape.any_of or ():
        try:
            candidate = _generate(
                member,
                active,
                variant,
                policy=policy,
            )
        except MockGenerationError:
            continue
        if member.validate(candidate) is None:
            return candidate
    raise MockGenerationError(f'no union member can be generated for {shape_name(base)}')


def _array(
    base: BaseShape,
    shape: ArrayShape,
    active: frozenset[int],
    variant: int,
    policy: _Policy,
) -> list[object]:
    minimum = shape.min_items.value if shape.min_items is not None else 0
    maximum = shape.max_items.value if shape.max_items is not None else None
    if shape.items is None:
        return _implicit_array(shape, minimum, maximum, variant, policy)

    examples = _examples(shape.items) if policy.examples else []
    preferred = list(examples)
    if policy.default and shape.items.default is not None:
        preferred.append(shape.items.default.raw)
    preferred.extend(item.raw for item in shape.items.enum or ())
    configured = policy.generation.collection_size
    count = max(minimum, configured) if configured is not None else minimum or len(examples)
    if maximum is not None:
        count = min(count, maximum)
    unique = shape.unique_items is not None and shape.unique_items.value
    values: list[object] = []
    for candidate in preferred:
        if len(values) >= count:
            break
        if not unique or not _contains(values, candidate):
            values.append(candidate)

    attempt = 0
    stalled = 0
    while len(values) < count and stalled < _MAX_STALLED_ATTEMPTS:
        candidate = _generate(
            shape.items,
            active,
            variant + attempt,
            policy=_Policy(
                examples=False,
                default=policy.default and not unique,
                generation=policy.generation,
                key=policy.key,
            ),
        )
        attempt += 1
        if not unique or not _contains(values, candidate):
            values.append(candidate)
            stalled = 0
        else:
            stalled += 1
    if len(values) < count or not _valid(base, values):
        raise MockGenerationError(f'cannot synthesize array for {shape_name(base)}')
    return values


def _contains(values: list[object], candidate: object) -> bool:
    return any(same_value(value, candidate) for value in values)


def _implicit_array(
    shape: ArrayShape,
    minimum: int,
    maximum: int | None,
    variant: int,
    policy: _Policy,
) -> list[object]:
    configured = policy.generation.collection_size
    count = max(minimum, configured) if configured is not None else minimum
    if maximum is not None:
        count = min(count, maximum)
    if shape.unique_items is None or not shape.unique_items.value:
        return [None] * count
    values: list[object] = []
    if count:
        values.append(None)
        values.extend(f'mock{variant + index}' for index in range(count - 1))
    if not _valid(shape.base, values):
        raise MockGenerationError(f'cannot synthesize array for {shape_name(shape.base)}')
    return values


def _valid(base: BaseShape, value: object) -> bool:
    return base.validate(value) is None


def _string(base: BaseShape, shape: StringShape, variant: int) -> str:
    minimum = shape.min_length.value if shape.min_length is not None else 0
    maximum = shape.max_length.value if shape.max_length is not None else None
    pattern = shape.pattern.value.pattern if shape.pattern is not None else None
    literal = None if pattern is None else _LITERAL_PREFIX.match(pattern)
    candidates = [] if literal is None else [literal.group(1)]
    if variant and maximum:
        candidates.append(format(variant, 'x')[-maximum:])
    candidates.extend(
        [
            f'string{variant}' if variant else 'string',
            f'mock{variant}',
            str(variant),
            '',
            'a' * max(1, minimum),
            '0' * max(1, minimum),
            'A' * max(1, minimum),
            'title',
            'x-mock',
        ]
    )
    for candidate in candidates:
        adjusted = candidate + 'a' * max(0, minimum - len(candidate))
        if maximum is not None:
            adjusted = adjusted[:maximum]
        if _valid(base, adjusted):
            return adjusted
    raise MockGenerationError(f'cannot synthesize string pattern {pattern!r} for {shape_name(base)}')


def _on_grid(low: Fraction | int | None, high: Fraction | int | None, step: Fraction, variant: int) -> Fraction:
    """The variant-th multiple of *step* that whichever bounds exist allow.

    Both numeric kinds walk this same grid; they differ only in what sets the
    spacing. An integer's is its `multipleOf` or 1 -- every branch the old
    `_integer` spelled out separately is this with a step of one.
    """
    if low is not None and high is not None:
        first, last = _ceil(low / step), _floor(high / step)
        factor = first + variant % max(1, last - first + 1)
    elif low is not None:
        factor = _ceil(low / step) + variant
    elif high is not None:
        factor = _floor(high / step) - variant
    else:
        factor = variant
    return factor * step


def _first_valid(base: BaseShape, candidates: list[Fraction], label: str) -> int | Decimal:
    """The first candidate that *base* accepts, as an exact number."""
    for candidate in candidates:
        try:
            value: int | Decimal = candidate.numerator if candidate.denominator == 1 else _finite_decimal(candidate)
        except MockGenerationError:
            continue
        if _valid(base, value):
            return value
    raise MockGenerationError(f'cannot synthesize {label} for {shape_name(base)}')


def _integer(base: BaseShape, shape: IntegerShape, variant: int) -> int:
    low = shape.minimum.value if shape.minimum is not None else None
    high = shape.maximum.value if shape.maximum is not None else None
    stride = abs(shape.multiple_of.value.numerator) if shape.multiple_of is not None else 1
    candidates = [_on_grid(low, high, Fraction(stride), variant)]
    candidates.extend(Fraction(value) for value in (0, 1, -1, high, low) if value is not None)
    return cast('int', _first_valid(base, candidates, 'integer'))


def _number(base: BaseShape, shape: NumberShape, variant: int) -> int | Decimal:
    low = shape.minimum.value if shape.minimum is not None else None
    high = shape.maximum.value if shape.maximum is not None else None
    if shape.multiple_of is not None:
        candidates = [_on_grid(low, high, abs(shape.multiple_of.value), variant)]
    elif low is not None and high is not None:
        # Interpolated rather than gridded: with no `multipleOf` there is no
        # spacing the document asked for, and thousandths of the span stay
        # inside bounds that a grid anchored at zero would have to round off.
        candidates = [low + (high - low) * Fraction(variant % 1001, 1000), low]
    elif low is not None:
        candidates = [low + variant, low]
    elif high is not None:
        candidates = [high - variant, high]
    else:
        candidates = [Fraction(variant)]
    candidates.extend(value for value in (Fraction(0), Fraction(1), Fraction(-1), high, low) if value is not None)
    return _first_valid(base, candidates, 'number')


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _floor(value: Fraction) -> int:
    return value.numerator // value.denominator


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
    coefficient = abs(value.numerator) * (2 ** (scale - twos)) * (5 ** (scale - fives))
    digits = tuple(int(digit) for digit in str(coefficient))
    return Decimal((value.numerator < 0, digits, -scale))


def _object(
    base: BaseShape,
    shape: ObjectShape,
    active: frozenset[int],
    variant: int,
    policy: _Policy,
) -> dict[str, object]:
    value: dict[str, object] = {}
    properties = shape.properties or {}
    for name, prop in properties.items():
        if not prop.required:
            continue
        value[name] = _generate(
            prop.base,
            active,
            variant,
            policy=policy,
        )
    for name, prop in properties.items():
        if prop.required or not _include_optional(policy, base.id, name):
            continue
        try:
            value[name] = _generate(prop.base, active, variant, policy=policy)
        except MockGenerationError:
            continue
    if shape.discriminator is not None:
        name = shape.discriminator.value
        if shape.discriminator_value is not None:
            value[name] = shape.discriminator_value.raw
        elif name in properties and name not in value:
            value[name] = _generate(
                properties[name].base,
                active,
                variant,
                policy=policy,
            )
    _fill_object(
        shape,
        value,
        active,
        variant,
        policy,
    )
    if not _valid(base, value):
        raise MockGenerationError(f'cannot synthesize object for {shape_name(base)}')
    return value


def _fill_object(
    shape: ObjectShape,
    value: dict[str, object],
    active: frozenset[int],
    variant: int,
    policy: _Policy,
) -> None:
    properties = shape.properties or {}
    minimum = shape.min_properties.value if shape.min_properties is not None else 0
    for name, prop in properties.items():
        if len(value) >= minimum:
            break
        if name not in value:
            try:
                value[name] = _generate(
                    prop.base,
                    active,
                    variant,
                    policy=policy,
                )
            except MockGenerationError:
                continue
    while len(value) < minimum:
        generated = _pattern_value(
            shape,
            set(value),
            active,
            variant + len(value),
            policy,
        )
        if generated is not None:
            name, item = generated
            value[name] = item
        elif not shape.pattern_properties and (
            shape.additional_properties is None or shape.additional_properties.value
        ):
            value[_unused_name(value)] = 'string'
        else:
            break


def _unused_name(value: dict[str, object]) -> str:
    """A synthesized property name that is not already taken.

    `property{len(value) + 1}` on its own is not enough: a *declared* property
    may already carry that name, and overwriting it leaves the count unchanged,
    so the `minProperties` loop above never terminates. `minProperties: 3` over
    a property literally named `property2` is enough to reach it. The failure is
    a hang inside a request handler rather than an error -- it spins the event
    loop, so it takes the whole application down and not just that request.
    """
    index = len(value) + 1
    while f'property{index}' in value:
        index += 1
    return f'property{index}'


def _pattern_value(
    shape: ObjectShape,
    used: set[str],
    active: frozenset[int],
    variant: int,
    policy: _Policy,
) -> tuple[str, object] | None:
    patterns = shape.pattern_properties or {}
    for pattern in patterns.values():
        prefix = _LITERAL_PREFIX.match(pattern.pattern.pattern)
        start = '' if prefix is None else prefix.group(1)
        candidates = (f'{start}mock{variant}', f'x-mock{variant}', f'property{variant}', f'mock{variant}', 'a')
        for name in candidates:
            if name in used or pattern.pattern.search(name) is None:
                continue
            first = next((item for item in patterns.values() if item.pattern.search(name) is not None), None)
            if first is pattern:
                return name, _generate(
                    pattern.base,
                    active,
                    variant,
                    policy=policy,
                )
    return None


def _initial_variant(options: GenerationOptions, key: str) -> int:
    if options.seed is None:
        return 0
    digest = hashlib.sha256(f'{options.seed}\0{key}'.encode()).digest()
    return int.from_bytes(digest[:4], 'big')


def _include_optional(policy: _Policy, shape_id: int, name: str) -> bool:
    probability = policy.generation.optional_probability
    if probability <= 0:
        return False
    if probability >= 1:
        return True
    digest = hashlib.sha256(f'{policy.generation.seed}\0{policy.key}\0{shape_id}\0{name}'.encode()).digest()
    score = int.from_bytes(digest[:8], 'big') / (1 << 64)
    return score < probability
