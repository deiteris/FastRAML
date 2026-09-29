"""What a Python class or annotation says, read without deciding anything about RAML.

Pure functions over pydantic models, dataclasses, `TypedDict`s and type
annotations: their fields, the keys each field is read from and written
under, their bases, and whether `None` is among their values. `Walk` decides
what each becomes; this only reads.
"""

from __future__ import annotations

import dataclasses
import types
import typing
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, get_args, get_origin

import annotated_types
from pydantic import AliasChoices, AliasPath, BaseModel, Field
from pydantic.fields import FieldInfo

from raml_document.from_pydantic.values import as_yaml
from raml_document.model import UNSET, Unset, Yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

try:  # pragma: no cover - the name moved between pydantic releases
    from pydantic_core import PydanticUndefined
except ImportError:  # pragma: no cover
    from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined, no-redef]

__all__ = [
    'PydanticUndefined',
    'Shape',
    'admits_none',
    'computed_of',
    'expanded',
    'fields_of',
    'forbids_extra',
    'is_default_doc',
    'is_model',
    'is_union',
    'literal_value',
    'models_in',
    'own_fields',
    'supertypes',
    'unwrap_annotated',
    'whole',
    'wire_name',
    'without_none',
]


@dataclass(frozen=True, slots=True)
class Shape:
    """How a response writes its models, where that is not how they are read.

    `Shape()` is what pydantic writes by default: each field under its
    serialization alias, a `None` written as null. `by_alias=False` and
    `exclude_none=True` are FastAPI's `response_model_by_alias` and
    `response_model_exclude_none`.
    """

    by_alias: bool = True
    exclude_none: bool = False

    @property
    def suffix(self) -> str:
        """What a model declared in this shape is named after: `UserOutput`, `UserOutputByName`..."""
        return f'Output{"" if self.by_alias else "ByName"}{"NoNulls" if self.exclude_none else ""}'


# -- models ---------------------------------------------------------------------


def is_model(annotation: Any) -> bool:
    """Is `annotation` a class pydantic validates as an object of named fields?"""
    if not isinstance(annotation, type):
        return False
    return issubclass(annotation, BaseModel) or dataclasses.is_dataclass(annotation) or typing.is_typeddict(annotation)


def fields_of(model: type) -> dict[str, FieldInfo]:
    """A model's fields as the `FieldInfo` pydantic would build for each.

    A pydantic model or dataclass has them already. A standard dataclass or a
    `TypedDict` is read from its annotations: pydantic validates both, so a
    FastAPI body or response may be either.
    """
    if issubclass(model, BaseModel):
        return dict(model.model_fields)
    ready = getattr(model, '__pydantic_fields__', None)
    if ready is not None:
        return dict(ready)
    known = _ANNOTATED_FIELDS.get(model)
    if known is None:
        known = _ANNOTATED_FIELDS[model] = _annotated_fields(model)
    return known


#: `_annotated_fields` per class, built once and let go with the class.
_ANNOTATED_FIELDS: Final[weakref.WeakKeyDictionary[type, dict[str, FieldInfo]]] = weakref.WeakKeyDictionary()


def _annotated_fields(model: type) -> dict[str, FieldInfo]:
    """`fields_of` for a class pydantic has built nothing for."""
    hints = typing.get_type_hints(model, include_extras=True)
    out: dict[str, FieldInfo] = {}
    if dataclasses.is_dataclass(model):
        for item in dataclasses.fields(model):
            default: Any = PydanticUndefined
            if item.default is not dataclasses.MISSING:
                default = item.default
            elif item.default_factory is not dataclasses.MISSING:
                default = Field(default_factory=item.default_factory)
            out[item.name] = FieldInfo.from_annotated_attribute(hints[item.name], default)
        return out
    required_keys: frozenset[str] = getattr(model, '__required_keys__', frozenset())
    for name, hint in hints.items():
        # `NotRequired[X]` and `Required[X]` are read off the annotation, which
        # decides over `__required_keys__`: under `from __future__ import
        # annotations` the class body held strings, and `TypedDict` could not
        # see either marker in them.
        bare, required = hint, name in required_keys
        while get_origin(bare) in (typing.NotRequired, typing.Required):
            required = get_origin(bare) is typing.Required
            bare = get_args(bare)[0]
        out[name] = FieldInfo.from_annotated_attribute(bare, PydanticUndefined if required else None)
    return out


def computed_of(model: type) -> dict[str, Any]:
    """A model's computed fields; a dataclass or `TypedDict` has none this reads."""
    return dict(getattr(model, 'model_computed_fields', {}))


def own_fields(model: type) -> set[str]:
    """The fields declared on this class, not the ones it inherits -- a redeclared one among them."""
    return set(getattr(model, '__annotations__', {})) & set(fields_of(model))


def supertypes(model: type) -> list[type[BaseModel]]:
    """The bases RAML should inherit from, which is not every Python base.

    `BaseModel` itself is not a RAML type. Neither is `RootModel`, whose job is
    to *be* its single field rather than to be a supertype of anything. Neither
    is a generic, in either form. `Page`, with its fields still typed `T`, is
    the base pydantic gives `Page[Book]` -- inheriting it would type `items` as
    `any[]` and hide the `Book[]` the parametrisation resolved. `Page[Book]`
    names one parametrisation of many, so a model deriving from it keeps its
    properties inline as well.
    """
    return [base for base in model.__bases__ if _is_supertype(base)]


def _is_supertype(base: Any) -> bool:
    if not (isinstance(base, type) and issubclass(base, BaseModel)) or base is BaseModel:
        return False
    if any(ancestor.__name__ == 'RootModel' for ancestor in base.__mro__):
        return False
    generic = getattr(base, '__pydantic_generic_metadata__', {})
    return generic.get('origin') is None and not generic.get('parameters')


def forbids_extra(model: type) -> bool:
    config = getattr(model, 'model_config', None) or getattr(model, '__pydantic_config__', None) or {}
    return bool(config.get('extra') == 'forbid')


def is_default_doc(model: type) -> bool:
    """Is `__doc__` the one pydantic or `dataclass` generates for a class with no docstring?"""
    return (model.__doc__ or '').startswith(f'{model.__name__}(')


def models_in(annotation: Any) -> Iterator[type]:
    """Every model an annotation names, however deeply it is nested."""
    annotation, _ = unwrap_annotated(annotation)
    if is_model(annotation):
        yield annotation
    for arg in get_args(annotation):
        yield from models_in(arg)


# -- keys -----------------------------------------------------------------------


def wire_name(name: str, info: FieldInfo, *, output: Shape | None) -> str | None:
    """The key a field travels under: read by its validation alias, written by its serialization one.

    `AliasChoices` reads the first choice that is a key; `None` where there is
    none, since an `AliasPath` reaches into a nested value no property names.
    A shape that does not write by alias writes the field's own name.
    """
    if output is not None and not output.by_alias:
        return name
    chosen = info.serialization_alias if output is not None else info.validation_alias
    if isinstance(chosen, AliasChoices):
        return next((choice for choice in chosen.choices if isinstance(choice, str)), None)
    if isinstance(chosen, AliasPath):
        return None
    if isinstance(chosen, str):
        return chosen
    return info.alias or name


def whole(spec: Any) -> bool:
    """Does an include or exclude entry take the whole field, rather than narrow it?"""
    return spec is True or spec is Ellipsis


# -- annotations ----------------------------------------------------------------


def unwrap_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """`Annotated[X, a, b]` -> `(X, (a, b))`; anything else -> `(it, ())`."""
    if get_origin(annotation) is typing.Annotated:
        args = get_args(annotation)
        return args[0], args[1:]
    return annotation, ()


def is_union(annotation: Any) -> bool:
    return get_origin(annotation) in (typing.Union, types.UnionType)


def admits_none(annotation: Any) -> bool:
    """Is `None` one of the values `annotation` allows?"""
    annotation, _ = unwrap_annotated(annotation)
    if annotation in (None, type(None), Any):
        return True
    return is_union(annotation) and any(admits_none(arg) for arg in get_args(annotation))


def without_none(annotation: Any) -> Any:
    """`X | None` -> `X`; `X | Y | None` -> `X | Y`; anything else unchanged."""
    if not is_union(annotation):
        return annotation
    kept = tuple(arg for arg in get_args(annotation) if arg is not type(None))
    if not kept or len(kept) == len(get_args(annotation)):
        return annotation
    return kept[0] if len(kept) == 1 else typing.Union[kept]  # noqa: UP007 - built from a tuple


def literal_value(annotation: Any) -> Yaml | Unset:
    """The single value a `Literal[...]` names, or `UNSET` if it names none."""
    annotation, _ = unwrap_annotated(annotation)
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if len(args) == 1:
            return as_yaml(args[0])
    return UNSET


def expanded(metadata: Any) -> Iterator[Any]:
    """`metadata` with each group opened up and each placeholder left out.

    `constr(...)` arrives as one `StringConstraints` and `conint(...)` as one
    `Interval`, each a group of the `MinLen`, `Ge`... it stands for; the `con*`
    helpers also leave a `None` for every constraint not given.
    """
    for item in metadata or ():
        if isinstance(item, annotated_types.GroupedMetadata):
            yield from expanded(item)
        elif item is not None:
            yield item
