"""A fastRAML shape -> JSON Schema draft-07.

Follows go-raml's `converter/jsonschema.go` visitor, which is the reference this
project is modelled on. The decisions worth naming, because none is obvious:

- **The entry point must be unwrapped.** go-raml refuses otherwise
  (`entrypoint shape must be unwrapped`), and for the same reason fastRAML's
  `validate()` asserts it: an un-flattened shape shows only what its own
  declaration wrote.
- **Named types become `definitions`**, and the result is a `$ref` to one. A name
  is **occupied before its body is walked**, so a type that reaches itself finds
  the entry already there and emits a `$ref` instead of recurring.
- **A `RecursiveShape` emits an empty schema plus a `$ref`**, never a schema built
  from its base. Every RAML type may carry custom facets, and those can be
  recursive too, so building the base first is how the walk fails to terminate.
  Per JSON Schema, a `$ref` ignores its siblings anyway.
- **Numbers never pass through `float` on the way in.** A fastRAML facet holds a
  `Fraction` built from the raw text, so an integral one is written as an integer
  and the rest as their shortest decimal.

What RAML says and JSON Schema cannot carry is listed in `Conversion.dropped`.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

from fastraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.jsonschema_ import JsonShape
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

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.types.base import BaseShape

__all__ = ['SCHEMA_VERSION', 'Conversion', 'to_json_schema']

SCHEMA_VERSION: Final = 'http://json-schema.org/draft-07/schema'

#: RFC 2616 date-time, as go-raml spells it. JSON Schema has no format for it.
RFC2616: Final = (
    r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([0-3][0-9]) '
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) ([0-9]{4})'
    r' ([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9] GMT$'
)
#: `datetime-only` has no JSON Schema format either.
DATETIME_ONLY: Final = r'^[0-9]{4}-(?:0[0-9]|1[0-2])-(?:[0-2][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$'


class Conversion:
    """One walk: the definitions it produced and what it could not carry."""

    __slots__ = ('definitions', 'dropped', 'key')

    def __init__(self, key: str = 'definitions') -> None:
        #: Where definitions live and what a `$ref` points into. Draft-07 spells
        #: it `definitions`; 2019-09 and after spell it `$defs`, and a consumer
        #: expecting one will not follow the other.
        self.key = key
        self.definitions: dict[str, Any] = {}
        self.dropped: list[str] = []

    def drop(self, at: str, what: str) -> None:
        self.dropped.append(f'{at}: {what}')

    # -- entry ---------------------------------------------------------------

    def convert(self, base: BaseShape, at: str = '$') -> dict[str, Any]:
        """`base` as a standalone draft-07 schema, definitions and all.

        Invariant I12 (docs/02 § 4): the entry point must be unwrapped. An
        un-flattened shape carries only what its own declaration wrote, so a
        schema built from one silently omits every inherited facet. go-raml
        refuses the same case, with `entrypoint shape must be unwrapped`.
        """
        base._assert_unwrapped()  # noqa: SLF001 - one package, one invariant
        name = base.name or 'root'
        # Occupied before the body is walked, so a type that reaches itself
        # finds the entry already there and closes the loop with a `$ref`.
        self.definitions[name] = {}
        self.definitions[name] = self.inline(base, at)
        return {
            '$schema': SCHEMA_VERSION,
            '$ref': f'#/{self.key}/{name}',
            self.key: self.definitions,
        }

    def inline(self, base: BaseShape, at: str) -> dict[str, Any]:
        """One nested declaration, written where it stands.

        **Only the entry point and recursion heads become definitions.** A
        property's shape carries the *property's* name, so treating every named
        shape as a definition would hoist `name`, `tags` and every `items` into
        `definitions` and then refer to each from the single place it is used.
        """
        return self._body(base, at)

    # -- one shape -----------------------------------------------------------

    def _body(self, base: BaseShape, at: str) -> dict[str, Any]:
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            # Empty, not `_common`: a custom facet may be recursive too, and
            # building the base first is how this fails to terminate. A `$ref`
            # ignores its siblings, so nothing is lost by leaving them out.
            head = shape.head
            if head.name is not None and head.name not in self.definitions:
                self.definitions[head.name] = {}
                self.definitions[head.name] = self._body(head, at)
            return {'$ref': f'#/{self.key}/{head.name}'} if head.name else {}

        node = self._common(base)
        if isinstance(shape, JsonShape):
            # Already a JSON Schema, held as the text it was written as: hand
            # back the author's own schema rather than a projection of it.
            try:
                written = json.loads(shape.raw)
            except ValueError:
                self.drop(at, 'the external schema is not JSON; rendered as an empty schema')
                return node
            return {**node, **written} if isinstance(written, dict) else node
        builder = _BUILDERS.get(type(shape))
        if builder is None:
            if shape is not None and not isinstance(shape, AnyShape):
                self.drop(at, f'{type(shape).__name__} has no JSON Schema form')
            return node
        builder(self, node, shape, at)
        return node

    def _common(self, base: BaseShape) -> dict[str, Any]:
        """What every RAML declaration may carry, whatever its kind."""
        node: dict[str, Any] = {}
        if base.display_name is not None:
            node['title'] = base.display_name.value
        if base.description is not None:
            node['description'] = base.description.value
        if base.default is not None:
            node['default'] = base.default.raw
        examples = [example.data.raw for example in _examples(base)]
        if examples:
            node['examples'] = examples
        if base.enum:
            node['enum'] = [member.raw for member in base.enum]
        return node


def _examples(base: BaseShape) -> Iterator[Any]:
    """Every example, singular and plural.

    Through `entries()` rather than `values`: with `examples: !include e.raml`
    the examples live on the fragment and `values` is empty, so reading it does
    not fail -- it silently sees nothing.
    """
    if base.example is not None:
        yield base.example
    if base.examples is not None:
        yield from base.examples.entries().values()


# -- per-kind builders --------------------------------------------------------


def _number(value: Fraction | float) -> int | float:
    """A facet's exact value as JSON. Integral rationals stay integers."""
    if isinstance(value, Fraction):
        return int(value) if value.denominator == 1 else float(value)
    return value


def _facet(node: dict[str, Any], key: str, facet: Any, *, number: bool = False) -> None:
    if facet is not None:
        node[key] = _number(facet.value) if number else facet.value


def _object(conv: Conversion, node: dict[str, Any], shape: ObjectShape, at: str) -> None:
    node['type'] = 'object'
    _facet(node, 'minProperties', shape.min_properties)
    _facet(node, 'maxProperties', shape.max_properties)
    _facet(node, 'additionalProperties', shape.additional_properties)
    if shape.properties:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, prop in shape.properties.items():
            properties[name] = conv.inline(prop.base, f'{at}.{name}')
            if prop.required:
                required.append(name)
        node['properties'] = properties
        if required:
            node['required'] = required
    if shape.pattern_properties:
        # Already the bare regex: the `/…/` delimiters are RAML syntax marking a
        # name as a pattern, and P2 strips them at decode. go-raml slices them
        # off here because its own model keeps them.
        node['patternProperties'] = {
            key: conv.inline(prop.base, f'{at}.{key}') for key, prop in shape.pattern_properties.items()
        }


def _array(conv: Conversion, node: dict[str, Any], shape: ArrayShape, at: str) -> None:
    node['type'] = 'array'
    _facet(node, 'minItems', shape.min_items)
    _facet(node, 'maxItems', shape.max_items)
    _facet(node, 'uniqueItems', shape.unique_items)
    if shape.items is not None:
        node['items'] = conv.inline(shape.items, f'{at}[]')


def _union(conv: Conversion, node: dict[str, Any], shape: UnionShape, at: str) -> None:
    node['anyOf'] = [conv.inline(member, f'{at}|') for member in shape.any_of or ()]


def _string(conv: Conversion, node: dict[str, Any], shape: StringShape, at: str) -> None:  # noqa: ARG001
    node['type'] = 'string'
    _facet(node, 'minLength', shape.min_length)
    _facet(node, 'maxLength', shape.max_length)
    if shape.pattern is not None:
        node['pattern'] = shape.pattern.value.pattern


def _numeric(kind: str) -> Any:
    def build(conv: Conversion, node: dict[str, Any], shape: Any, at: str) -> None:  # noqa: ARG001
        node['type'] = kind
        _facet(node, 'minimum', shape.minimum, number=True)
        _facet(node, 'maximum', shape.maximum, number=True)
        _facet(node, 'multipleOf', shape.multiple_of, number=True)

    return build


def _boolean(conv: Conversion, node: dict[str, Any], shape: BooleanShape, at: str) -> None:  # noqa: ARG001
    node['type'] = 'boolean'


def _nil(conv: Conversion, node: dict[str, Any], shape: NilShape, at: str) -> None:  # noqa: ARG001
    node['type'] = 'null'


def _file(conv: Conversion, node: dict[str, Any], shape: FileShape, at: str) -> None:
    node['type'] = 'string'
    _facet(node, 'minLength', shape.min_length)
    _facet(node, 'maxLength', shape.max_length)
    node['contentEncoding'] = 'base64'
    if shape.file_types:
        # JSON Schema allows one content media type; RAML allows a list.
        node['contentMediaType'] = shape.file_types[0].value
        if len(shape.file_types) > 1:
            conv.drop(at, f'{len(shape.file_types) - 1} more fileTypes; JSON Schema carries one')


def _datetime(conv: Conversion, node: dict[str, Any], shape: DateTimeShape, at: str) -> None:  # noqa: ARG001
    node['type'] = 'string'
    spelling = shape.format.value if shape.format is not None else None
    if spelling == 'rfc2616':
        node['pattern'] = RFC2616
    else:
        node['format'] = 'date-time'


def _datetime_only(conv: Conversion, node: dict[str, Any], shape: DateTimeOnlyShape, at: str) -> None:  # noqa: ARG001
    node['type'] = 'string'
    node['pattern'] = DATETIME_ONLY


def _formatted(spelling: str) -> Any:
    def build(conv: Conversion, node: dict[str, Any], shape: Any, at: str) -> None:  # noqa: ARG001
        node['type'] = 'string'
        node['format'] = spelling

    return build


#: Shape class -> the builder that fills its node. `AnyShape` is deliberately
#: absent: `any` carries no `type` at all, which is what an empty schema means.
_BUILDERS: Final[dict[type, Any]] = {
    ObjectShape: _object,
    ArrayShape: _array,
    UnionShape: _union,
    StringShape: _string,
    IntegerShape: _numeric('integer'),
    NumberShape: _numeric('number'),
    BooleanShape: _boolean,
    NilShape: _nil,
    FileShape: _file,
    DateTimeShape: _datetime,
    DateTimeOnlyShape: _datetime_only,
    DateOnlyShape: _formatted('date'),
    TimeOnlyShape: _formatted('time'),
}


def to_json_schema(base: BaseShape, *, key: str = 'definitions') -> tuple[dict[str, Any], list[str]]:
    """`base` as draft-07, and everything JSON Schema could not carry.

    `key` names the section definitions live in. Draft-07 says `definitions`;
    pass `$defs` for a consumer that reads 2019-09 or later.
    """
    conversion = Conversion(key)
    return conversion.convert(base), conversion.dropped
