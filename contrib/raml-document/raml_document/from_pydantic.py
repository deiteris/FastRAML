r"""Pydantic models -> RAML type declarations, read from the models themselves.

**Not through JSON Schema.** `model_json_schema()` is a projection built for a
different target, and it drops things RAML can carry:

- `Decimal(max_digits=8, decimal_places=2)` becomes
  `anyOf: [number, string with a 60-character regex]`; RAML wants
  `multipleOf: 0.01`, which is right there in `FieldInfo.metadata`.
- `dict[int, str]` becomes a bare `additionalProperties`, losing the key type;
  RAML can say `/^[-+]?\\d+$/: string`.
- A discriminated union's `mapping` keys are stringified, so an integer tag
  arrives as `'1'` and the RAML no longer parses against an `integer` property.

It also adds work of its own -- `$defs`, `$ref`, `anyOf` for nullability -- that
has to be undone to get back to what the annotation already said. Reading
`model_fields` and the annotations is both shorter and more faithful.

What cannot be carried is reported on `Walk.dropped`, never dropped in silence.
Constraints RAML has no facet for -- an exclusive bound, `strict=True`, an
`AfterValidator` -- are named there rather than approximated.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import types
import typing
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Literal, get_args, get_origin

import annotated_types
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from raml_document.model import UNSET, TypeDecl, Unset, Yaml

__all__ = ['SCALARS', 'Walk']

#: A Python type -> the RAML built-in that carries it.
SCALARS: Final[dict[Any, str]] = {
    str: 'string',
    int: 'integer',
    float: 'number',
    decimal.Decimal: 'number',
    bool: 'boolean',
    bytes: 'file',
    datetime.datetime: 'datetime',
    datetime.date: 'date-only',
    datetime.time: 'time-only',
    uuid.UUID: 'string',
    type(None): 'nil',
    Any: 'any',
}

#: Where a `dict` key type narrows RAML's pattern-any property.
#:
#: The `/…/` delimiters are what make a property name a *pattern* rather than a
#: literal key, and RAML matches one unanchored, so each is anchored by hand.
#: `//` is the bare pattern-any: an empty regex between the delimiters.
_ANY_KEY: Final = '//'
_KEY_PATTERNS: Final[dict[Any, str]] = {
    int: r'/^[-+]?\d+$/',
    uuid.UUID: r'/^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/',
}


@dataclass(slots=True)
class Walk:
    """One traversal: the models it reached, and what it could not carry.

    `types` accumulates every model met, keyed by the RAML name it was given, so
    a model referenced from three places is declared once and referred to by
    name. That is also what makes recursion fall out: a self-reference is a name
    that is already registered by the time the field is reached.
    """

    types: dict[str, TypeDecl] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    #: model -> the RAML name it was registered under, which is not always
    #: `__name__`: two models may share one.
    _names: dict[type, str] = field(default_factory=dict)

    def drop(self, at: str, what: str) -> None:
        self.dropped.append(f'{at}: {what}')

    # -- models ---------------------------------------------------------------

    def model(self, model: type[BaseModel]) -> str:
        """Register `model` and every model it reaches; return its RAML name."""
        known = self._names.get(model)
        if known is not None:
            return known
        name = self._name_for(model)
        self._names[model] = name
        # Reserved before the body is walked, so a self-reference finds it.
        self.types[name] = TypeDecl(type='object')
        self.types[name] = self._body(model, name)
        return name

    def _name_for(self, model: type[BaseModel]) -> str:
        """`__name__`, qualified by module where two models would collide."""
        name = model.__name__
        if name not in self.types:
            return name
        qualified = f'{model.__module__.rsplit(".", 1)[-1]}.{name}'.replace('.', '_')
        self.drop(name, f'a second model of this name is declared as {qualified}')
        return qualified

    def _body(self, model: type[BaseModel], at: str) -> TypeDecl:
        decl = TypeDecl(type='object')
        config = getattr(model, 'model_config', {})
        if config.get('extra') == 'forbid':
            decl.additional_properties = False
        if model.__doc__ and not _is_default_doc(model):
            decl.description = model.__doc__.strip()

        root = model.model_fields.get('root')
        if root is not None and len(model.model_fields) == 1:
            # A RootModel: the declaration *is* its single field.
            return self.field(root, f'{at}.root')

        for name, info in model.model_fields.items():
            decl.properties[info.alias or name] = self.optional(self.field(info, f'{at}.{name}'), info)
        return decl

    # -- fields ---------------------------------------------------------------

    def field(self, info: FieldInfo, at: str) -> TypeDecl:
        """One `FieldInfo` -> one declaration, constraints and all."""
        decl = self.annotation(info.annotation, at, discriminator=self._tag_name(info, at))
        self.constrain(decl, info.metadata, at)
        if info.description:
            decl.description = info.description
        if info.examples:
            decl.examples = {f'e{index}': _as_yaml(value) for index, value in enumerate(info.examples)}
        return decl

    def optional(self, decl: TypeDecl, info: FieldInfo) -> TypeDecl:
        """Apply a field's optionality and its default to its declaration.

        Separate from `field` because the two are not the same question and only
        one of them is about the annotation. `field` reads what a value must
        look like; this reads whether the value has to be there at all, which is
        a property of the *position* -- a model property, a query parameter, a
        header. Returns the declaration, so the two compose in one expression.

        **RAML's default in every one of those positions is `required: true`.**
        A caller that reads the annotation and stops there renders an optional
        parameter as a mandatory one, and the document is then stricter than the
        code it describes -- wrong in the direction nothing complains about,
        because every request the tests send does carry the parameter.

        `default: ~` is not written for a field defaulting to `None`: RAML would
        take it as a value, and the field is simply absent.
        """
        if info.is_required():
            return decl
        decl.required = False
        if info.default is not None and info.default is not PydanticUndefined:
            decl.default = _as_yaml(info.default)
        return decl

    def _tag_name(self, info: FieldInfo, at: str) -> str | None:
        """The discriminator property a field names, if it names one at all.

        Two spellings land in two places, which is why both are read:
        `Field(discriminator='kind')` sets `info.discriminator`, while
        `Annotated[Union[...], Discriminator('kind')]` leaves a `Discriminator`
        in `info.metadata`. Reading only the first renders the second as a plain
        union -- valid RAML that says less than the model does.

        A `Discriminator` may instead wrap a **callable** that computes the tag.
        RAML names a property, so that has no spelling: the union stays an
        ordinary one, which is correct and less precise.
        """
        for tag in (info.discriminator, *(info.metadata or ())):
            if isinstance(tag, str):
                return tag
            inner = getattr(tag, 'discriminator', None)
            if isinstance(inner, str):
                return inner
            if inner is not None:
                self.drop(at, 'a callable discriminator has no RAML form; rendered as a plain union')
                return None
        return None

    def annotation(  # noqa: PLR0911 - one return per kind reads better than nesting
        self, annotation: Any, at: str, *, discriminator: str | None = None
    ) -> TypeDecl:
        """One type annotation -> one declaration."""
        annotation, extra = _unwrap_annotated(annotation)
        origin = get_origin(annotation)

        if origin in (typing.Union, types.UnionType):
            return self.union(get_args(annotation), at, discriminator=discriminator)
        if origin is Literal:
            return _literal(get_args(annotation))
        if origin in (list, set, frozenset, tuple):
            return self.sequence(annotation, origin, at)
        if origin is dict:
            return self.mapping(get_args(annotation), at)
        if isinstance(annotation, type):
            if issubclass(annotation, BaseModel):
                return TypeDecl(type=self.model(annotation))
            if issubclass(annotation, enum.Enum):
                return _enum(annotation)
            # Down the MRO, so the *most derived* match wins. `issubclass` in
            # table order would call a `bool` an integer, because Python makes
            # `bool` a subclass of `int` and no RAML author means that; the same
            # order keeps `datetime` from being read as its base `date`.
            for base in annotation.__mro__:
                spelling = SCALARS.get(base)
                if spelling is not None:
                    return TypeDecl(type=spelling)
        if annotation in SCALARS:
            return TypeDecl(type=SCALARS[annotation])
        if annotation is None:
            return TypeDecl(type='nil')

        self.drop(at, f'no RAML spelling for {annotation!r}; rendered as any')
        decl = TypeDecl(type='any')
        self.constrain(decl, extra, at)
        return decl

    def union(self, args: tuple[Any, ...], at: str, *, discriminator: str | None) -> TypeDecl:
        if discriminator is not None:
            return self.tagged_union(args, at, discriminator)
        members = [self.annotation(arg, at) for arg in args]
        spellings = [member.type for member in members if member.type is not None]
        if len(spellings) != len(members):
            self.drop(at, 'a union member has no type expression; rendered as any')
            return TypeDecl(type='any')
        return TypeDecl(type=' | '.join(dict.fromkeys(spellings)))

    def tagged_union(self, args: tuple[Any, ...], at: str, discriminator: str) -> TypeDecl:
        """A discriminated union -> a synthesised RAML base its members inherit.

        RAML splits what pydantic states in one place: `discriminator` names the
        tag property and lives on a *base* type, `discriminatorValue` identifies
        each subtype. Pydantic has no base, so one is made here.

        **The tag's value and type come from each member's own `Literal`**, which
        is where they are exact. A discriminated union's OpenAPI `mapping` keys
        are strings, so an integer tag read from there produces
        `discriminatorValue: '1'` against an `integer` property -- RAML that does
        not parse.

        The use site is the union of the members, not the base: `discriminator`
        MUST NOT appear on a union type, so the base carries the declaration and
        the union is what selects.
        """
        members: list[tuple[str, Yaml]] = []
        tag_type: str | None = None
        for arg in args:
            model, _ = _unwrap_annotated(arg)
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                self.drop(at, 'a discriminated union member is not a model')
                return TypeDecl(type='any')
            name = self.model(model)
            tag = model.model_fields.get(discriminator)
            value = _literal_value(tag.annotation) if tag is not None else UNSET
            if isinstance(value, Unset):
                self.drop(at, f'member {name} has no Literal {discriminator!r} to identify it')
                return TypeDecl(type='any')
            members.append((name, value))
            spelling = self.types[name].properties.get(discriminator)
            tag_type = spelling.type if spelling is not None else tag_type

        base = f'{at.rsplit(".", 1)[-1].title()}Base'
        self.types[base] = TypeDecl(
            type='object',
            discriminator=discriminator,
            properties={discriminator: TypeDecl(type=tag_type or 'string')},
        )
        for name, value in members:
            member = self.types[name]
            member.type = base
            member.discriminator_value = value
            # `Literal['cat'] = 'cat'` carries a default, so the tag arrives
            # optional. In a tagged union it is not: the default applies once a
            # member is chosen, and choosing is what the tag is for.
            tag_decl = member.properties.get(discriminator)
            if tag_decl is not None:
                tag_decl.required = None
                tag_decl.default = UNSET
        return TypeDecl(type=' | '.join(name for name, _ in members))

    def sequence(self, annotation: Any, origin: Any, at: str) -> TypeDecl:
        args = get_args(annotation)
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):  # noqa: PLR2004 - `tuple[X, ...]`
            self.drop(at, 'a fixed-length tuple has no RAML form; rendered as an array')
        items = self.annotation(args[0], f'{at}[]') if args else TypeDecl(type='any')
        decl = TypeDecl(type='array', items=items)
        if origin in (set, frozenset):
            decl.unique_items = True
        # `string[]` where the item has a plain spelling, which is what a union
        # member and a nested `items` both need.
        if items.type is not None and items.render() == items.type and '|' not in items.type:
            return TypeDecl(type=f'{items.type}[]', unique_items=decl.unique_items)
        return decl

    def mapping(self, args: tuple[Any, ...], at: str) -> TypeDecl:
        """`dict[K, V]` -> RAML's pattern-any property, narrowed by the key type.

        JSON Schema has one `additionalProperties` and no place for `K`, so this
        is one of the things reading the model recovers.
        """
        key, value = (*args, Any, Any)[:2]
        key, _ = _unwrap_annotated(key)
        pattern = _ANY_KEY if key in (str, Any) else _KEY_PATTERNS.get(key)
        if pattern is None:
            self.drop(at, f'no RAML property pattern for key type {key!r}; any key accepted')
            pattern = _ANY_KEY
        return TypeDecl(type='object', properties={pattern: self.annotation(value, f'{at}.{pattern}')})

    # -- constraints ----------------------------------------------------------

    def constrain(self, decl: TypeDecl, metadata: Any, at: str) -> None:
        """Apply `FieldInfo.metadata` to a declaration.

        Length means different facets on different kinds -- `minLength` on a
        string, `minItems` on an array -- so the declaration decides, which it
        can because it was built first.
        """
        sequence = decl.type == 'array' or (decl.type or '').endswith('[]')
        for item in metadata or ():
            match item:
                case annotated_types.Ge(ge=bound):
                    decl.minimum = _as_number(bound, decl, at, self)
                case annotated_types.Le(le=bound):
                    decl.maximum = _as_number(bound, decl, at, self)
                case annotated_types.Gt(gt=bound):
                    self.drop(at, f'exclusive minimum {bound!r} has no RAML facet; not written')
                case annotated_types.Lt(lt=bound):
                    self.drop(at, f'exclusive maximum {bound!r} has no RAML facet; not written')
                case annotated_types.MultipleOf(multiple_of=step):
                    decl.multiple_of = _as_number(step, decl, at, self)
                case annotated_types.MinLen(min_length=length):
                    _set_length(decl, 'min', length, sequence=sequence)
                case annotated_types.MaxLen(max_length=length):
                    _set_length(decl, 'max', length, sequence=sequence)
                case annotated_types.Len(min_length=low, max_length=high):
                    _set_length(decl, 'min', low, sequence=sequence)
                    if high is not None:
                        _set_length(decl, 'max', high, sequence=sequence)
                case _:
                    self._general(decl, item, at)

    def _general(self, decl: TypeDecl, item: Any, at: str) -> None:
        """Pydantic's own metadata objects, which carry several fields at once."""
        if getattr(item, 'discriminator', None) is not None:
            return  # read by `_tag_name`, which reports what it cannot use
        pattern = getattr(item, 'pattern', None)
        if pattern is not None:
            decl.pattern = pattern
        places = getattr(item, 'decimal_places', None)
        if places is not None:
            # The facet JSON Schema cannot express: two decimal places is a step
            # of 0.01, which RAML states exactly.
            decl.multiple_of = float(decimal.Decimal(1).scaleb(-places))
        digits = getattr(item, 'max_digits', None)
        if digits is not None and places is not None:
            decl.maximum = float(decimal.Decimal(10) ** (digits - places) - decimal.Decimal(1).scaleb(-places))
        if pattern is None and places is None and digits is None:
            self.drop(at, f'no RAML facet for {item!r}')


def _set_length(decl: TypeDecl, end: str, length: int | None, *, sequence: bool) -> None:
    if length is None:
        return
    if sequence:
        setattr(decl, f'{end}_items', length)
    else:
        setattr(decl, f'{end}_length', length)


def _as_number(value: Any, decl: TypeDecl, at: str, walk: Walk) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    # A date bound, say: RAML has no minimum on a date.
    walk.drop(at, f'bound {value!r} has no RAML facet on {decl.type}; not written')
    return None


def _unwrap_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """`Annotated[X, a, b]` -> `(X, (a, b))`; anything else -> `(it, ())`."""
    if get_origin(annotation) is typing.Annotated:
        args = get_args(annotation)
        return args[0], args[1:]
    return annotation, ()


def _literal_value(annotation: Any) -> Yaml | Unset:
    """The single value a `Literal[...]` names, or `UNSET` if it names none."""
    annotation, _ = _unwrap_annotated(annotation)
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if len(args) == 1:
            return _as_yaml(args[0])
    return UNSET


def _literal(args: tuple[Any, ...]) -> TypeDecl:
    values = [_as_yaml(arg) for arg in args]
    kinds = {SCALARS.get(type(arg), 'string') for arg in args}
    return TypeDecl(type=kinds.pop() if len(kinds) == 1 else 'any', enum=values)


def _enum(annotation: type[enum.Enum]) -> TypeDecl:
    values = [_as_yaml(member.value) for member in annotation]
    kinds = {SCALARS.get(type(member.value), 'string') for member in annotation}
    return TypeDecl(type=kinds.pop() if len(kinds) == 1 else 'any', enum=values)


def _as_yaml(value: Any) -> Yaml:
    """A Python value as something `yaml.safe_dump` writes."""
    if isinstance(value, enum.Enum):
        return _as_yaml(value.value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (list, tuple, set)):
        return [_as_yaml(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_yaml(item) for key, item in value.items()}
    return str(value)


def _is_default_doc(model: type[BaseModel]) -> bool:
    """Is `__doc__` the one pydantic generates for a model with no docstring?"""
    return (model.__doc__ or '').startswith(f'{model.__name__}(')


try:  # pragma: no cover - the name moved between pydantic releases
    from pydantic_core import PydanticUndefined
except ImportError:  # pragma: no cover
    from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined, no-redef]
