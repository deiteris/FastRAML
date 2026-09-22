"""The eleven scalar kinds.

Each reads the facets doc 05 section 3 gives it and passes everything else up
to `KindBase`, which files it as a custom facet value.

Numeric bounds never pass through `float`. Integer bounds are `int`, and a
number's bounds and `multipleOf` are `Fraction`s built from the written text:
`Fraction(1.1)` embeds the binary-float error, and `multipleOf: 1.1` would then
reject `2.2` (docs/05 section 3.1).

`check` and `validate` are methods here rather than functions in
`types/validate.py`, because dispatch on kind is what a method already is and
doc 05's `Shape` protocol declares both. What they share lives in
`types/values.py`, a leaf, so using it cannot close a cycle back through the
pass driver (docs/02 section 2).
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.errors import Accumulator
from fastraml.parser.facets import (
    make_fraction_facet,
    make_int_facet,
    make_pattern_facet,
    make_seq_facet,
    make_string_facet,
    scalar_str,
)
from fastraml.types.base import KindBase
from fastraml.types.values import (
    INTEGER_RANGES,
    as_fraction,
    check_non_negative,
    failure,
    is_multiple_of,
    parse_rfc2616,
    parse_rfc3339,
    type_name,
    valid_date_only,
    valid_datetime_only,
    valid_time_only,
)
from fastraml.yamlnode import NodeKind, node_error

if TYPE_CHECKING:
    from collections.abc import Container
    from fractions import Fraction
    from typing import Any

    from fastraml.errors import RamlError
    from fastraml.types.base import BaseShape, ScalarFacet
    from fastraml.yamlnode import Node

__all__ = [
    'AnyShape',
    'BooleanShape',
    'DateOnlyShape',
    'DateTimeOnlyShape',
    'DateTimeShape',
    'FileShape',
    'IntegerShape',
    'NilShape',
    'NumberShape',
    'ScalarKind',
    'StringShape',
    'TimeOnlyShape',
]

#: The two `format` values a `datetime` may take (docs/05 section 3).
DATETIME_FORMATS: Final = frozenset({'rfc3339', 'rfc2616'})

#: `format` on an integer, and the size it stands for. Doubles as the
#: inheritance compatibility check: a subtype's size may not exceed its
#: parent's. Deviation D2 forbids crossing this table with the number one.
INTEGER_FORMATS: Final = {'int8': 0, 'int16': 1, 'int32': 2, 'int': 2, 'int64': 3, 'long': 3}

#: `format` on a number.
NUMBER_FORMATS: Final = frozenset({'float', 'double'})

#: A `fileTypes` entry: RFC 6838 `type/subtype`, the wildcard `*/*` aside.
_MEDIA_TYPE: Final = re.compile(r'\A[A-Za-z0-9][\w.+-]*/[A-Za-z0-9][\w.+-]*\Z')


def _decode_base64(text: str) -> bytes:
    """The payload a base64 `file` value stands for.

    Undecodable text falls back to its UTF-8 bytes rather than failing: doc 10
    section 5 gives `file` no wellformedness rule, only length bounds, and
    inventing one here would reject values the spec accepts.
    """
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return text.encode('utf-8', 'surrogatepass')


def _disordered(
    low: ScalarFacet[Any] | None, high: ScalarFacet[Any] | None
) -> tuple[ScalarFacet[Any], ScalarFacet[Any]] | None:
    """The pair, when a min/max bound is unsatisfiable; `None` when it is fine.

    Returning the pair rather than a boolean is what lets the callers raise
    without re-narrowing two `Optional`s the test has already settled.
    """
    if low is not None and high is not None and low.value > high.value:
        return low, high
    return None


def _bounds_error(base: BaseShape, message: str, low: ScalarFacet[Any], high: ScalarFacet[Any]) -> RamlError:
    # Positioned at the *lower* bound, which is written first, so the reader
    # sees the offending pair from its top.
    return failure(message, base.location, low.key_pos, info={'min': str(low.value), 'max': str(high.value)})


def _check_lengths(base: BaseShape, low: ScalarFacet[int] | None, high: ScalarFacet[int] | None) -> None:
    """`minLength`/`maxLength`: non-negative, and ordered (docs/10 section 2)."""
    accumulator = Accumulator()
    for name, facet in (('minLength', low), ('maxLength', high)):
        if facet is not None:
            accumulator.add(check_non_negative(name, facet.value, base.location, facet.value_pos))
    pair = _disordered(low, high)
    if pair is not None:
        accumulator.add(_bounds_error(base, 'minLength exceeds maxLength', *pair))
    accumulator.raise_if_any()


def _check_format(base: BaseShape, declared: ScalarFacet[str] | None, allowed: Container[str], kind: str) -> None:
    """Deviation D2: the two numeric format tables do not mix."""
    if declared is not None and declared.value not in allowed:
        raise failure(
            'unknown format', base.location, declared.value_pos, info={'format': declared.value, 'type': kind}
        )


def _check_numeric(
    base: BaseShape,
    minimum: ScalarFacet[Any] | None,
    maximum: ScalarFacet[Any] | None,
    multiple_of: ScalarFacet[Fraction] | None,
) -> None:
    """The two rules `number` and `integer` share (docs/10 section 2)."""
    pair = _disordered(minimum, maximum)
    if pair is not None:
        raise _bounds_error(base, 'minimum exceeds maximum', *pair)
    if multiple_of is not None and multiple_of.value == 0:
        raise failure('multipleOf must not be zero', base.location, multiple_of.value_pos)


def _validate_numeric(  # noqa: PLR0913 - three facets, and each names itself at the call site
    base: BaseShape,
    number: Fraction,
    path: str,
    *,
    minimum: ScalarFacet[Any] | None,
    maximum: ScalarFacet[Any] | None,
    multiple_of: ScalarFacet[Fraction] | None,
) -> None:
    """Bounds and `multipleOf`, compared exactly (docs/10 section 5.3).

    Every comparison is `Fraction` against `Fraction` or `int`. Nothing here
    goes through `float`, which is what lets `multipleOf: 1.1` accept `2.2`.
    """
    if minimum is not None and number < minimum.value:
        raise failure(
            'value is below the minimum',
            base.location,
            base.value_pos,
            info={'path': path, 'value': str(number), 'minimum': str(minimum.value)},
        )
    if maximum is not None and number > maximum.value:
        raise failure(
            'value is above the maximum',
            base.location,
            base.value_pos,
            info={'path': path, 'value': str(number), 'maximum': str(maximum.value)},
        )
    if multiple_of is not None and not is_multiple_of(number, multiple_of.value):
        raise failure(
            'value is not a multiple',
            base.location,
            base.value_pos,
            info={'path': path, 'value': str(number), 'multipleOf': str(multiple_of.value)},
        )


class ScalarKind(KindBase):
    """A kind whose values are single scalars rather than structures.

    `check` does nothing by default: most scalar kinds hold no facet that can
    contradict another. The four that do — string, number, integer, file —
    override it (docs/10 section 2).
    """

    __slots__ = ()

    def is_scalar(self) -> bool:
        return True

    def check(self) -> None:
        return

    def wrong_type(self, value: Any, path: str, expected: str) -> RamlError:
        return failure(
            'invalid type',
            self.base.location,
            self.base.value_pos,
            info={'path': path, 'expected': expected, 'found': type_name(value)},
        )


class AnyShape(ScalarKind):
    """`any` — no facets, and every value conforms."""

    __slots__ = ()

    def validate(self, value: Any, path: str) -> None:  # noqa: ARG002 - `any` conforms to everything, by definition
        return


class NilShape(ScalarKind):
    """`nil` — the only conforming value is null."""

    __slots__ = ()

    def validate(self, value: Any, path: str) -> None:
        if value is not None:
            raise self.wrong_type(value, path, 'nil')


class BooleanShape(ScalarKind):
    __slots__ = ()

    def validate(self, value: Any, path: str) -> None:
        # Identity, not `isinstance`: there are exactly two booleans, and
        # `isinstance(True, int)` is what this has to avoid saying yes to.
        if value is not True and value is not False:
            raise self.wrong_type(value, path, 'boolean')


class _DateKind(ScalarKind):
    """The three date kinds, which differ only in the grammar they accept."""

    __slots__ = ()

    #: The RAML type name, which doubles as the grammar's name in a diagnostic.
    GRAMMAR: ClassVar[str] = ''

    def accepts(self, text: str) -> bool:
        raise NotImplementedError

    def validate(self, value: Any, path: str) -> None:
        if not isinstance(value, str):
            raise self.wrong_type(value, path, self.GRAMMAR)
        if not self.accepts(value):
            raise failure(
                'invalid date',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'expected': self.GRAMMAR, 'value': value},
            )


class DateOnlyShape(_DateKind):
    __slots__ = ()
    GRAMMAR: ClassVar[str] = 'date-only'

    def accepts(self, text: str) -> bool:
        return valid_date_only(text)


class TimeOnlyShape(_DateKind):
    __slots__ = ()
    GRAMMAR: ClassVar[str] = 'time-only'

    def accepts(self, text: str) -> bool:
        return valid_time_only(text)


class DateTimeOnlyShape(_DateKind):
    __slots__ = ()
    GRAMMAR: ClassVar[str] = 'datetime-only'

    def accepts(self, text: str) -> bool:
        return valid_datetime_only(text)


class DateTimeShape(ScalarKind):
    """`datetime`, whose `format` chooses between RFC 3339 and RFC 2616."""

    __slots__ = ('format',)

    def __init__(self, base: BaseShape) -> None:
        super().__init__(base)
        self.format: ScalarFacet[str] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            if key.value == 'format':
                # The value is checked in P10, not here: doc 10 owns the rule and
                # an inherited format has to be resolved first.
                self.format = make_string_facet(self.base._raml, key, value, self.base.location)  # noqa: SLF001
            else:
                rest.append(key)
                rest.append(value)
        super().decode_facets(rest)

    def check(self) -> None:
        _check_format(self.base, self.format, DATETIME_FORMATS, 'datetime')

    def validate(self, value: Any, path: str) -> None:
        if not isinstance(value, str):
            raise self.wrong_type(value, path, 'datetime')
        # RFC 3339 by default; `format: rfc2616` selects the other grammar and
        # they share nothing, so this is a choice rather than a fallback.
        rfc2616 = self.format is not None and self.format.value == 'rfc2616'
        if not (parse_rfc2616(value) if rfc2616 else parse_rfc3339(value)):
            raise failure(
                'invalid date',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'expected': 'rfc2616' if rfc2616 else 'rfc3339', 'value': value},
            )


class StringShape(ScalarKind):
    """`string`, with a compiled `pattern` and the two length bounds."""

    __slots__ = ('max_length', 'min_length', 'pattern')

    def __init__(self, base: BaseShape) -> None:
        super().__init__(base)
        self.pattern: ScalarFacet[re.Pattern[str]] | None = None
        self.min_length: ScalarFacet[int] | None = None
        self.max_length: ScalarFacet[int] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        raml, location = self.base._raml, self.base.location  # noqa: SLF001
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            match key.value:
                case 'pattern':
                    self.pattern = make_pattern_facet(raml, key, value, location)
                case 'minLength':
                    self.min_length = make_int_facet(raml, key, value, location)
                case 'maxLength':
                    self.max_length = make_int_facet(raml, key, value, location)
                case _:
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)

    def check(self) -> None:
        _check_lengths(self.base, self.min_length, self.max_length)

    def validate(self, value: Any, path: str) -> None:
        if not isinstance(value, str):
            raise self.wrong_type(value, path, 'string')
        if self.min_length is not None and len(value) < self.min_length.value:
            raise failure(
                'value is too short',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'length': len(value), 'minLength': self.min_length.value},
            )
        if self.max_length is not None and len(value) > self.max_length.value:
            raise failure(
                'value is too long',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'length': len(value), 'maxLength': self.max_length.value},
            )
        if self.pattern is not None and self.pattern.value.search(value) is None:
            # `search`, not `fullmatch`: the spec never says a `pattern:` facet
            # is anchored, and writes `^...$` itself wherever it means anchored
            # — `^.+@.+\..+$`, `^\d+\-\w+$`, `^\w{16}$`. Those anchors would be
            # noise under a full match. go-raml agrees: `regexp.Compile` on the
            # raw pattern and `MatchString`, which is Go's unanchored search
            # (docs/10 § 5.4).
            raise failure(
                'value does not match pattern',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'pattern': self.pattern.value.pattern, 'value': value},
            )


class NumberShape(ScalarKind):
    """`number`: exact bounds, and a `format` naming a binary width."""

    __slots__ = ('format', 'maximum', 'minimum', 'multiple_of')

    def __init__(self, base: BaseShape) -> None:
        super().__init__(base)
        self.minimum: ScalarFacet[Fraction] | None = None
        self.maximum: ScalarFacet[Fraction] | None = None
        self.multiple_of: ScalarFacet[Fraction] | None = None
        self.format: ScalarFacet[str] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        raml, location = self.base._raml, self.base.location  # noqa: SLF001
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            match key.value:
                case 'minimum':
                    self.minimum = make_fraction_facet(raml, key, value, location)
                case 'maximum':
                    self.maximum = make_fraction_facet(raml, key, value, location)
                case 'multipleOf':
                    self.multiple_of = make_fraction_facet(raml, key, value, location)
                case 'format':
                    self.format = make_string_facet(raml, key, value, location)
                case _:
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)

    def check(self) -> None:
        _check_numeric(self.base, self.minimum, self.maximum, self.multiple_of)
        _check_format(self.base, self.format, NUMBER_FORMATS, 'number')

    def validate(self, value: Any, path: str) -> None:
        # `bool` is a subclass of `int` in Python, so it reaches here as a
        # number unless it is refused by identity first (docs/10 section 5).
        number = None if value is True or value is False else as_fraction(value)
        if number is None or isinstance(value, str):
            # A numeric string is a number for `integer`, per doc 10 section 5's
            # number-preserving-decoder note, but not for `number`.
            raise self.wrong_type(value, path, 'number')
        _validate_numeric(
            self.base, number, path, minimum=self.minimum, maximum=self.maximum, multiple_of=self.multiple_of
        )


class IntegerShape(ScalarKind):
    """`integer`, whose bounds are integers but whose `multipleOf` is not.

    `multipleOf: 0.5` on an integer is legal and means every value is even.
    """

    __slots__ = ('format', 'maximum', 'minimum', 'multiple_of')

    def __init__(self, base: BaseShape) -> None:
        super().__init__(base)
        self.minimum: ScalarFacet[int] | None = None
        self.maximum: ScalarFacet[int] | None = None
        self.multiple_of: ScalarFacet[Fraction] | None = None
        self.format: ScalarFacet[str] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        raml, location = self.base._raml, self.base.location  # noqa: SLF001
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            match key.value:
                case 'minimum':
                    self.minimum = make_int_facet(raml, key, value, location)
                case 'maximum':
                    self.maximum = make_int_facet(raml, key, value, location)
                case 'multipleOf':
                    self.multiple_of = make_fraction_facet(raml, key, value, location)
                case 'format':
                    self.format = make_string_facet(raml, key, value, location)
                case _:
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)

    def check(self) -> None:
        _check_numeric(self.base, self.minimum, self.maximum, self.multiple_of)
        _check_format(self.base, self.format, INTEGER_FORMATS, 'integer')

    def validate(self, value: Any, path: str) -> None:
        number = None if value is True or value is False else as_fraction(value)
        if number is None:
            raise self.wrong_type(value, path, 'integer')
        if number.denominator != 1:
            # `4.0` is an integer and `4.5` is not: doc 10 section 5 accepts a
            # float or Decimal whose value is integral.
            raise failure(
                'value is not an integer',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'value': str(number)},
            )
        _validate_numeric(
            self.base, number, path, minimum=self.minimum, maximum=self.maximum, multiple_of=self.multiple_of
        )
        if self.format is not None:
            low, high = INTEGER_RANGES[self.format.value]
            if not low <= number <= high:
                raise failure(
                    'value is outside the format range',
                    self.base.location,
                    self.base.value_pos,
                    info={'path': path, 'value': str(number), 'format': self.format.value},
                )


class FileShape(ScalarKind):
    """`file`, which shares its length bounds with `string` and adds a list of
    accepted media types.
    """

    __slots__ = ('file_types', 'max_length', 'min_length')

    def __init__(self, base: BaseShape) -> None:
        super().__init__(base)
        self.file_types: list[ScalarFacet[str]] | None = None
        self.min_length: ScalarFacet[int] | None = None
        self.max_length: ScalarFacet[int] | None = None

    def decode_facets(self, pairs: list[Node]) -> None:
        raml, location = self.base._raml, self.base.location  # noqa: SLF001
        rest: list[Node] = []
        for index in range(0, len(pairs), 2):
            key, value = pairs[index], pairs[index + 1]
            match key.value:
                case 'fileTypes':
                    if value.kind is not NodeKind.SEQUENCE:
                        raise node_error('fileTypes must be a sequence', location, value)
                    self.file_types = [make_seq_facet(raml, item, location, scalar_str) for item in value.content]
                case 'minLength':
                    self.min_length = make_int_facet(raml, key, value, location)
                case 'maxLength':
                    self.max_length = make_int_facet(raml, key, value, location)
                case _:
                    rest.append(key)
                    rest.append(value)
        super().decode_facets(rest)

    def check(self) -> None:
        _check_lengths(self.base, self.min_length, self.max_length)
        for declared in self.file_types or ():
            if declared.value != '*/*' and _MEDIA_TYPE.match(declared.value) is None:
                raise failure(
                    'invalid media type', self.base.location, declared.value_pos, info={'fileType': declared.value}
                )

    def validate(self, value: Any, path: str) -> None:
        # `fileTypes` is deliberately not checked against the value: a base64
        # blob carries no media type of its own. The facet's wellformedness is
        # checked above, and enforcing it needs a content type, which only the
        # body that transported the value has (Phase 5).
        if isinstance(value, bytes):
            size = len(value)
        elif isinstance(value, str):
            size = len(_decode_base64(value))
        else:
            raise self.wrong_type(value, path, 'file')
        # In bytes, not characters: doc 10 section 5 is explicit about it, and
        # for base64 the two differ by about a third.
        if self.min_length is not None and size < self.min_length.value:
            raise failure(
                'value is too short',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'bytes': size, 'minLength': self.min_length.value},
            )
        if self.max_length is not None and size > self.max_length.value:
            raise failure(
                'value is too long',
                self.base.location,
                self.base.value_pos,
                info={'path': path, 'bytes': size, 'maxLength': self.max_length.value},
            )
