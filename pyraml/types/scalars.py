"""The eleven scalar kinds.

Each reads the facets doc 05 section 3 gives it and passes everything else up
to `KindBase`, which files it as a custom facet value. In Phase 2 only
`decode_facets` has a body: `inherit`, `alias_to`, `check`, `validate` and
`clone` arrive with Phases 4 and 8, and the stubs name the phase.

Numeric bounds never pass through `float`. Integer bounds are `int`, and a
number's bounds and `multipleOf` are `Fraction`s built from the written text:
`Fraction(1.1)` embeds the binary-float error, and `multipleOf: 1.1` would then
reject `2.2` (docs/05 section 3.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pyraml.parser.facets import (
    make_fraction_facet,
    make_int_facet,
    make_pattern_facet,
    make_seq_facet,
    make_string_facet,
    scalar_str,
)
from pyraml.types.base import KindBase
from pyraml.yamlnode import NodeKind, node_error

if TYPE_CHECKING:
    import re
    from fractions import Fraction

    from pyraml.types.base import BaseShape, ScalarFacet
    from pyraml.yamlnode import Node

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


class ScalarKind(KindBase):
    """A kind whose values are single scalars rather than structures."""

    __slots__ = ()

    def is_scalar(self) -> bool:
        return True


class AnyShape(ScalarKind):
    """`any` — no facets, and every value conforms."""

    __slots__ = ()


class NilShape(ScalarKind):
    """`nil` — the only conforming value is null."""

    __slots__ = ()


class BooleanShape(ScalarKind):
    __slots__ = ()


class DateOnlyShape(ScalarKind):
    __slots__ = ()


class TimeOnlyShape(ScalarKind):
    __slots__ = ()


class DateTimeOnlyShape(ScalarKind):
    __slots__ = ()


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
                # The value is checked in P8, not here: doc 10 owns the rule and
                # an inherited format has to be resolved first.
                self.format = make_string_facet(self.base._raml, key, value, self.base.location)  # noqa: SLF001
            else:
                rest += (key, value)
        super().decode_facets(rest)


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
                    rest += (key, value)
        super().decode_facets(rest)


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
                    rest += (key, value)
        super().decode_facets(rest)


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
                    rest += (key, value)
        super().decode_facets(rest)


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
                    rest += (key, value)
        super().decode_facets(rest)
