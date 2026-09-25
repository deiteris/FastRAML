"""A value for a shape: the author's own, composed from the author's, or synthesized.

docs/16 § 8.1 is the contract. In short, and in this order:

1. **Declared.** The shape's own examples that validate, skipping one its
   author marked `strict: false`; else its own `default`; else an `enum`
   member. A supertype's example is never tried: a subtype may narrow a facet
   or add a required property, so nothing guarantees the parent's example fits,
   and examples are not inherited (docs/07 § 4).
2. **Composed.** An object from its properties' values, an array from its
   items', a union from its first member that yields one -- each by these same
   rules, so a property's own example is used where it has one.
3. **Synthesized.** A deterministic scalar that satisfies the facets, only when
   `SampleOptions.synthesize` allows it. With it off a value is built from
   declared data alone, and one that would contain none is an error.

Every value is validated against the shape before it is returned, and returned
as a copy that shares no container with the model.
"""

from __future__ import annotations

import base64
import random
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import isfinite
from typing import TYPE_CHECKING, cast

from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.examples import examples_of
from fastraml.types.jsonschema_ import projected
from fastraml.types.scalars import (
    AnyShape,
    BooleanShape,
    DateOnlyShape,
    DateTimeOnlyShape,
    DateTimeShape,
    FileShape,
    IntegerShape,
    NilShape,
    NumberShape,
    StringShape,
    TimeOnlyShape,
)
from fastraml.types.values import ValueSet, decimal_digits

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.types.base import BaseShape

__all__ = ['SampleError', 'SampleOptions', 'declared_values', 'named_example', 'sample']

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


class SampleError(ValueError):
    """No value the shape accepts could be produced under the options given."""


@dataclass(slots=True, frozen=True)
class SampleOptions:
    """How `sample` chooses among valid values.

    `seed` picks deterministically among declared examples and synthesized
    variants; `None` always picks the first. `collection_size` is the length
    an array aims for within its bounds. `optional_probability` is the chance,
    per property and decided by the seed, that an optional property is
    included. `synthesize=False` builds a value from declared data alone.
    """

    seed: int | str | None = None
    collection_size: int | None = None
    optional_probability: float = 0.0
    synthesize: bool = True

    def __post_init__(self) -> None:
        if self.collection_size is not None and self.collection_size < 0:
            raise ValueError('collection_size must be non-negative')
        if not isfinite(self.optional_probability) or not 0.0 <= self.optional_probability <= 1.0:
            raise ValueError('optional_probability must be between zero and one')


def declared_values(base: BaseShape) -> list[object]:
    """The values the author declared on *base* itself that *base* accepts.

    Its examples, in declaration order, without any marked `strict: false`;
    if none is left, its `default`. Never a supertype's (module docstring).
    """
    found = _own_examples(base)
    if not found and base.default is not None and base.validate(base.default.raw) is None:
        found.append(base.default.raw)
    return found


def named_example(base: BaseShape, name: str) -> object:
    """The example *base* declares under *name*, which *base* must accept.

    Chosen by name, so `strict: false` does not exclude it; validation still
    does. Only *base*'s own `examples:` are looked in.
    """
    found = base.examples.entries().get(name) if base.examples is not None else None
    if found is None or found.data is None:
        raise SampleError(f'unknown example {name!r} for {_name(base)}')
    return _checked(base, found.data.raw)


def sample(base: BaseShape, *, options: SampleOptions | None = None, key: str = '') -> object:
    """A deterministic value *base* accepts (module docstring).

    *key* varies the choice between call sites that share a seed, so two
    responses of one API differ; it has no effect without a seed.
    """
    options = options or SampleOptions()
    run = _Run(options, key)
    variant = 0 if options.seed is None else run.random.getrandbits(32)
    value = run.generate(base, frozenset(), variant)
    if not options.synthesize and not run.declared:
        raise SampleError(f'nothing declared to compose a value from for {_name(base)}')
    return _checked(base, value)


def _checked(base: BaseShape, value: object) -> object:
    error = base.validate(value)
    if error is not None:
        raise SampleError(f'could not produce a valid value for {_name(base)}: {error}')
    return _detach(value)


def _detach(value: object) -> object:
    """A copy sharing no container with *value*: the model's data is the parser's own."""
    if isinstance(value, dict):
        return {key: _detach(item) for key, item in cast('Mapping[str, object]', value).items()}
    if isinstance(value, list):
        return [_detach(item) for item in cast('list[object]', value)]
    return value


def _name(base: BaseShape) -> str:
    return base.name or (type(base.shape).__name__ if base.shape is not None else 'shape')


def _valid(base: BaseShape, value: object) -> bool:
    return base.validate(value) is None


class _Run:
    """One `sample` call: its options, its choices, and whether any declared value was used.

    Every seeded choice draws from one `random.Random`, seeded from the seed
    and the key. The walk visits properties in declaration order, so the same
    document, seed and key always make the same draws. It is built on the first
    draw: most calls, with no seed and no optional probability, make none.
    """

    __slots__ = ('_random', 'declared', 'key', 'options')

    def __init__(self, options: SampleOptions, key: str) -> None:
        self.options = options
        self.key = key
        self.declared = False
        self._random: random.Random | None = None

    @property
    def random(self) -> random.Random:
        if self._random is None:
            # Reproducible choice, not secrecy: the stdlib generator is the tool.
            self._random = random.Random(f'{self.options.seed}\0{self.key}')  # noqa: S311
        return self._random

    def generate(
        self,
        base: BaseShape,
        active: frozenset[int],
        variant: int,
        *,
        examples: bool = True,
        default: bool = True,
    ) -> object:
        declared = self._declared(base, variant, examples=examples, default=default)
        if declared is not _MISSING:
            self.declared = True
            return declared
        if base.id in active:
            raise SampleError(f'required recursive value has no finite example: {_name(base)}')
        view = projected(base)
        if view is not base:
            return self.generate(view, active, variant, examples=examples, default=default)
        concrete = base.shape
        if isinstance(concrete, RecursiveShape):
            raise SampleError(f'required recursive value has no finite example: {_name(base)}')
        active = active | {base.id}
        if isinstance(concrete, ObjectShape):
            return self._object(base, concrete, active, variant)
        if isinstance(concrete, ArrayShape):
            return self._array(base, concrete, active, variant)
        if isinstance(concrete, UnionShape):
            return self._union(base, concrete, active, variant)
        if not self.options.synthesize:
            raise SampleError(f'no declared value for {_name(base)}')
        return _scalar(base, concrete, variant)

    def _declared(self, base: BaseShape, variant: int, *, examples: bool, default: bool) -> object:
        # `examples=False` is an array's further items, after its declared ones.
        if examples:
            found = declared_values(base)
            if found:
                return found[variant % len(found)]
        elif default and base.default is not None and _valid(base, base.default.raw):
            return base.default.raw
        if base.enum:
            return base.enum[variant % len(base.enum)].raw
        return _MISSING

    def _union(self, base: BaseShape, shape: UnionShape, active: frozenset[int], variant: int) -> object:
        for member in shape.any_of or ():
            try:
                candidate = self.generate(member, active, variant)
            except SampleError:
                continue
            if _valid(member, candidate):
                return candidate
        raise SampleError(f'no union member can be sampled for {_name(base)}')

    def _array(self, base: BaseShape, shape: ArrayShape, active: frozenset[int], variant: int) -> list[object]:
        minimum = shape.min_items.value if shape.min_items is not None else 0
        maximum = shape.max_items.value if shape.max_items is not None else None
        if shape.items is None:
            return self._implicit_array(shape, minimum, maximum, variant)

        preferred = list(_own_examples(shape.items))
        examples = len(preferred)
        if shape.items.default is not None:
            preferred.append(shape.items.default.raw)
        preferred.extend(item.raw for item in shape.items.enum or ())
        configured = self.options.collection_size
        count = max(minimum, configured) if configured is not None else minimum or examples
        if maximum is not None:
            count = min(count, maximum)
        unique = shape.unique_items is not None and shape.unique_items.value
        values: list[object] = []
        # Under `same_value`, as `uniqueItems` itself: keyed, so a thousand items
        # cost a thousand lookups rather than half a million comparisons.
        seen = ValueSet()
        for candidate in preferred:
            if len(values) >= count:
                break
            if not unique or seen.add(candidate):
                values.append(candidate)
                self.declared = True

        attempt = 0
        stalled = 0
        while len(values) < count and stalled < _MAX_STALLED_ATTEMPTS:
            candidate = self.generate(shape.items, active, variant + attempt, examples=False, default=not unique)
            attempt += 1
            if not unique or seen.add(candidate):
                values.append(candidate)
                stalled = 0
            else:
                stalled += 1
        if len(values) < count or not _valid(base, values):
            raise SampleError(f'cannot sample an array for {_name(base)}')
        return values

    def _implicit_array(self, shape: ArrayShape, minimum: int, maximum: int | None, variant: int) -> list[object]:
        configured = self.options.collection_size
        count = max(minimum, configured) if configured is not None else minimum
        if maximum is not None:
            count = min(count, maximum)
        if count and not self.options.synthesize:
            raise SampleError(f'no declared items for {_name(shape.base)}')
        if shape.unique_items is None or not shape.unique_items.value:
            return [None] * count
        values: list[object] = []
        if count:
            values.append(None)
            values.extend(f'item{variant + index}' for index in range(count - 1))
        if not _valid(shape.base, values):
            raise SampleError(f'cannot sample an array for {_name(shape.base)}')
        return values

    def _object(self, base: BaseShape, shape: ObjectShape, active: frozenset[int], variant: int) -> dict[str, object]:
        value: dict[str, object] = {}
        properties = shape.properties or {}
        for name, prop in properties.items():
            if prop.required:
                value[name] = self.generate(prop.base, active, variant)
        for name, prop in properties.items():
            if not prop.required and self._include_optional():
                self._optional(value, name, prop.base, active, variant)
        if shape.discriminator is not None:
            name = shape.discriminator.value
            if shape.discriminator_value is not None:
                value[name] = shape.discriminator_value.raw
            elif name in properties and name not in value:
                value[name] = self.generate(properties[name].base, active, variant)
        self._fill_object(shape, value, active, variant)
        if not _valid(base, value):
            raise SampleError(f'cannot sample an object for {_name(base)}')
        return value

    def _optional(
        self, value: dict[str, object], name: str, base: BaseShape, active: frozenset[int], variant: int
    ) -> None:
        """Add an optional property if it can be sampled.

        Composing from declared data alone, only if it drew on some: an
        optional `meta: {}` built from nothing tells a reader nothing.
        """
        before, self.declared = self.declared, False
        try:
            item = self.generate(base, active, variant)
        except SampleError:
            item = _MISSING
        if item is not _MISSING and (self.options.synthesize or self.declared):
            value[name] = item
        self.declared = before or self.declared

    def _include_optional(self) -> bool:
        """Whether an optional property is included.

        Composing from declared data alone, every optional property that has
        some is: it is the author's, and it is what a reader wants to see.
        Otherwise the seeded probability decides.
        """
        if not self.options.synthesize:
            return True
        probability = self.options.optional_probability
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        return self.random.random() < probability

    def _fill_object(self, shape: ObjectShape, value: dict[str, object], active: frozenset[int], variant: int) -> None:
        properties = shape.properties or {}
        minimum = shape.min_properties.value if shape.min_properties is not None else 0
        for name, prop in properties.items():
            if len(value) >= minimum:
                break
            if name not in value:
                try:
                    value[name] = self.generate(prop.base, active, variant)
                except SampleError:
                    continue
        if not self.options.synthesize:
            return
        while len(value) < minimum:
            generated = self._pattern_value(shape, set(value), active, variant + len(value))
            if generated is not None:
                name, item = generated
                value[name] = item
            elif not shape.pattern_properties and (
                shape.additional_properties is None or shape.additional_properties.value
            ):
                value[_unused_name(value)] = 'string'
            else:
                break

    def _pattern_value(
        self, shape: ObjectShape, used: set[str], active: frozenset[int], variant: int
    ) -> tuple[str, object] | None:
        patterns = shape.pattern_properties or {}
        for pattern in patterns.values():
            prefix = _LITERAL_PREFIX.match(pattern.pattern.pattern)
            start = '' if prefix is None else prefix.group(1)
            candidates = (f'{start}name{variant}', f'x-name{variant}', f'property{variant}', f'name{variant}', 'a')
            for name in candidates:
                if name in used or pattern.pattern.search(name) is None:
                    continue
                first = next((item for item in patterns.values() if item.pattern.search(name) is not None), None)
                if first is pattern:
                    return name, self.generate(pattern.base, active, variant)
        return None


def _own_examples(base: BaseShape) -> list[object]:
    """`declared_values` without the default, for an array, which lists the default separately."""
    return [
        example.data.raw
        for example in examples_of(base)
        if example.data is not None
        and (example.strict is None or example.strict.value)
        and base.validate(example.data.raw) is None
    ]


def _unused_name(value: dict[str, object]) -> str:
    """A synthesized property name that is not already taken.

    `property{len(value) + 1}` on its own is not enough: a *declared* property
    may already carry that name, and overwriting it leaves the count unchanged,
    so the `minProperties` loop never terminates. `minProperties: 3` over a
    property literally named `property2` is enough to reach it.
    """
    index = len(value) + 1
    while f'property{index}' in value:
        index += 1
    return f'property{index}'


# -- scalars ------------------------------------------------------------------


def _scalar(base: BaseShape, shape: object | None, variant: int) -> object:  # noqa: PLR0911 - one return per kind
    fixed = _FIXED_SCALARS.get(type(shape), _MISSING)
    if fixed is not _MISSING:
        return fixed
    if isinstance(shape, BooleanShape):
        return bool(variant % 2)
    if isinstance(shape, StringShape):
        return _string(base, shape, variant)
    if isinstance(shape, IntegerShape):
        return _integer(base, shape, variant)
    if isinstance(shape, NumberShape):
        return _number(base, shape, variant)
    if isinstance(shape, DateTimeShape):
        return _RFC2616 if shape.format is not None and shape.format.value == 'rfc2616' else _RFC3339
    if isinstance(shape, FileShape):
        size = shape.min_length.value if shape.min_length is not None else 0
        return base64.b64encode(bytes(size)).decode('ascii')
    raise SampleError(f'unsupported shape for sampling: {_name(base)}')


def _string(base: BaseShape, shape: StringShape, variant: int) -> str:
    """A string within the length bounds that the pattern, if any, accepts.

    Best effort: a pattern is tried against its literal prefix and a fixed list
    of candidates, not solved. A pattern none of them matches is a `SampleError`.
    """
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
            f'value{variant}',
            str(variant),
            '',
            'a' * max(1, minimum),
            '0' * max(1, minimum),
            'A' * max(1, minimum),
            'title',
            'x-value',
        ]
    )
    for candidate in candidates:
        adjusted = candidate + 'a' * max(0, minimum - len(candidate))
        if maximum is not None:
            adjusted = adjusted[:maximum]
        if _valid(base, adjusted):
            return adjusted
    raise SampleError(f'cannot synthesize a string for pattern {pattern!r} of {_name(base)}')


def _on_grid(low: Fraction | int | None, high: Fraction | int | None, step: Fraction, variant: int) -> Fraction:
    """The variant-th multiple of *step* that whichever bounds exist allow.

    Both numeric kinds walk this same grid; they differ only in what sets the
    spacing. An integer's is its `multipleOf`, or 1.
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
    """The first candidate that *base* accepts, as an exact number.

    A candidate with no terminating decimal is skipped: a `Decimal` of it would
    be rounded, and numbers never pass through an approximation (docs/10 § 5).
    """
    for candidate in candidates:
        found = decimal_digits(candidate)
        if found is None:
            continue
        digits, scale = found
        # From text, which `Decimal` takes exactly; arithmetic would round to the context.
        value: int | Decimal = digits if scale == 0 else Decimal(f'{digits}E-{scale}')
        if _valid(base, value):
            return value
    raise SampleError(f'cannot synthesize {label} for {_name(base)}')


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
