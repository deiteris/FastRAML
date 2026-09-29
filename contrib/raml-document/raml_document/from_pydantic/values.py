"""Python values as the JSON they travel as: defaults, examples, enums, tags."""

from __future__ import annotations

import dataclasses
import decimal
import enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic_core import PydanticSerializationError, to_jsonable_python

from raml_document.from_pydantic.tables import SCALARS
from raml_document.model import TypeDecl

if TYPE_CHECKING:
    from raml_document.model import Yaml

__all__ = ['as_yaml', 'enum_decl', 'literal_decl']


def as_yaml(value: Any) -> Yaml:  # noqa: PLR0911 - one return per kind
    """A Python value as the JSON value it travels as, in a form `yaml.safe_dump` writes.

    Not `str()`: a `datetime` would read `2024-01-01 12:00:00`, which is not
    RFC 3339, and a model `x=1`, which is not an object. A `Decimal` is a
    number here, where pydantic's JSON mode would write a string, because
    the declaration it lands under is a RAML `number`.
    """
    if isinstance(value, enum.Enum):
        return as_yaml(value.value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [as_yaml(item) for item in value]
    if isinstance(value, dict):
        return {str(key): as_yaml(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        return as_yaml(value.model_dump(by_alias=True))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return as_yaml(dataclasses.asdict(value))
    try:
        # A date, a duration, a UUID, a URL: what pydantic writes for each.
        return as_yaml(to_jsonable_python(value))
    except PydanticSerializationError:
        return str(value)


def literal_decl(args: tuple[Any, ...]) -> TypeDecl:
    """`Literal[...]`: the built-in its values share, and the values as its `enum`."""
    return _enumerated(list(args))


def enum_decl(annotation: type[enum.Enum]) -> TypeDecl:
    """An `Enum` class: the built-in its members' values share, and the values."""
    return _enumerated([member.value for member in annotation])


def _enumerated(values: list[Any]) -> TypeDecl:
    kinds = {SCALARS.get(type(value), 'string') for value in values}
    return TypeDecl(type=kinds.pop() if len(kinds) == 1 else 'any', enum=[as_yaml(value) for value in values])
