"""`Walk`: one traversal from Python types to RAML declarations, and what it reached.

The public surface is small: `model`, `annotation`, `field` and `parameter` read
something, `subset` declares a model with fields left out, `output` switches to
what models *write*, and `types` and `dropped` are the result. Everything that
reads Python without deciding anything is in `introspect`, values in `values`,
constraints in `facets`, and unions -- which hold shared state -- in `unions`.
"""

from __future__ import annotations

import datetime
import enum
import typing
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

from pydantic import AliasChoices, AliasPath, BaseModel, Tag
from pydantic.fields import FieldInfo

from raml_document.annotations import annotate
from raml_document.from_pydantic import facets
from raml_document.from_pydantic.introspect import (
    PydanticUndefined,
    Shape,
    admits_none,
    computed_of,
    expanded,
    fields_of,
    forbids_extra,
    is_default_doc,
    is_model,
    is_union,
    models_in,
    own_fields,
    supertypes,
    unwrap_annotated,
    whole,
    wire_name,
    without_none,
)
from raml_document.from_pydantic.tables import ANY_KEY, KEY_PATTERNS, MAPPINGS, SCALARS, SEQUENCES, SETS, type_name
from raml_document.from_pydantic.unions import Unions
from raml_document.from_pydantic.values import as_yaml, enum_decl, literal_decl
from raml_document.model import UNSET, Parameters, TypeDecl, Unset, Yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['Walk']


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
    #: Classes this module cannot know, and the RAML built-in each is: a web
    #: framework's upload class as `file`, say. Read before `SCALARS`, down the
    #: MRO, so a subclass of one is the same built-in.
    scalars: dict[Any, str] = field(default_factory=dict)
    #: The `annotationTypes` the annotations applied so far need, filled like
    #: `types` as they are applied.
    annotation_types: dict[str, TypeDecl] = field(default_factory=dict)
    #: (model, output shape or None for input) -> the RAML name it was
    #: registered under, which is not always `__name__`: two models may share one.
    _names: dict[tuple[type, Shape | None], str] = field(default_factory=dict)
    _diverging: dict[tuple[type, Shape], bool] = field(default_factory=dict)
    _output: Shape | None = None
    _unions: Unions = field(init=False)

    def __post_init__(self) -> None:
        self._unions = Unions(self)

    def drop(self, at: str, what: str) -> None:
        """Report that `what`, at `at`, is not in the document."""
        message = f'{at}: {what}'
        if message not in self.dropped:
            self.dropped.append(message)

    @contextmanager
    def output(self, shape: Shape | None = None) -> Iterator[None]:
        """Walk what models *write* inside this block, in `shape` -- by default `Shape()`.

        For a response body. A model that writes what it reads keeps its one
        declaration; one that does not -- a `serialization_alias`, an
        `exclude=True` field, a `computed_field`, or a shape that changes it --
        is declared again as `{Name}{shape.suffix}`.
        """
        previous, self._output = self._output, shape or Shape()
        try:
            yield
        finally:
            self._output = previous

    def annotate(self, annotations: dict[str, Yaml], name: str, value: Yaml = None) -> None:
        """Apply `(name): value` from `raml_document.annotations`, declaring its type."""
        annotate(annotations, self.annotation_types, name, value)

    def unique(self, name: str) -> str:
        """`name`, or `name_2`, `name_3`... -- the first `types` does not hold."""
        candidate, number = name, 1
        while candidate in self.types:
            number += 1
            candidate = f'{name}_{number}'
        return candidate

    # -- models ---------------------------------------------------------------

    def model(self, model: type) -> str:
        """Register `model` and every model it reaches; return its RAML name.

        A model is a pydantic model, a dataclass or a `TypedDict`: each is an
        object with named fields, and pydantic validates each the same way.
        """
        output = self._output if self._output is not None and self._diverges(model, self._output) else None
        known = self._names.get((model, output))
        if known is not None:
            return known
        name = self._name_for(model, output=output)
        self._names[model, output] = name
        # Reserved before the body is walked, so a self-reference finds it.
        self.types[name] = TypeDecl(type='object')
        self.types[name] = self._declare(model, name, output=output)
        return name

    def subset(self, model: type, name: str, at: str, *, include: Any = None, exclude: Any = None) -> TypeDecl | None:
        """`model` as written with `include` and `exclude` applied, declared as `name`.

        FastAPI's `response_model_include` and `response_model_exclude`: a set
        of field names, or a dict whose values are `True` or narrow a field in
        turn. A narrowed field is written as `any` and reported, since what is
        left of it is a shape of its own. Every field is declared on the one
        type, inherited ones too: a RAML subtype cannot drop a property.
        Returns a reference to the declared type, or `None` if `model` is not a
        model.
        """
        if not is_model(model):
            return None
        fields, computed = fields_of(model), computed_of(model)
        kept: dict[str, bool] = {}  # field -> taken whole?
        for key in [*fields, *computed]:
            if include is not None and key not in include:
                continue
            narrowed = isinstance(include, dict) and not whole(include[key])
            if exclude is not None and key in exclude:
                if not isinstance(exclude, dict) or whole(exclude[key]):
                    continue
                narrowed = True
            kept[key] = not narrowed
        shape = self._output or Shape()
        properties = self._properties(model, name, set(kept), output=shape)
        for key, taken_whole in kept.items():
            if taken_whole:
                continue
            wire = self._wire(key, model, shape)
            if wire in properties:
                self.drop(at, f'{key} is written in part, which RAML cannot say; written as any')
                properties[wire] = TypeDecl(type='any', required=properties[wire].required)
        declared = self.unique(type_name(name))
        self.types[declared] = TypeDecl(type='object', properties=properties)
        return TypeDecl(type=declared)

    def _name_for(self, model: type, *, output: Shape | None) -> str:
        """`__name__`, qualified by module where two models would collide.

        Qualified by the last module segment first and the whole module path
        after, because two packages that each keep a `User` in `models.py` agree
        on the last segment. A number is the last resort -- two classes built by
        one factory share even their module -- and every step past the plain
        name is reported, since a reader looks for the class by its name.
        """
        name = type_name(f'{model.__name__}{output.suffix if output is not None else ""}')
        if name not in self.types:
            return name
        module = model.__module__
        for qualifier in (module.rsplit('.', 1)[-1], module):
            qualified = type_name(f'{qualifier}_{name}')
            if qualified not in self.types:
                break
        else:
            qualified = self.unique(qualified)
        self.drop(name, f'a second model of this name is declared as {qualified}')
        return qualified

    def _declare(self, model: type, at: str, *, output: Shape | None) -> TypeDecl:
        """The declaration `model` is registered under: its supertypes and its properties."""
        fields = fields_of(model)
        root = fields.get('root')
        if root is not None and len(fields) == 1 and issubclass(model, BaseModel):
            # A RootModel: the declaration *is* its single field.
            return self.field(root, f'{at}.root')

        parents = [self.model(base) for base in supertypes(model)]
        computed = computed_of(model)
        everything = {*fields, *computed}
        # Only what this class declares. RAML inherits the rest, so repeating an
        # inherited property would state twice what the supertype already says.
        own = {*own_fields(model), *(set(computed) & set(vars(model)))} if parents else everything
        properties = self._properties(model, at, own, output=output)
        # A field redeclared as its supertype already declares it says nothing
        # new. One redeclared any other way is a change RAML may refuse -- it
        # reads a redeclared property as a narrowing, and Python lets a
        # subclass retype a field however it likes -- so the class is declared
        # whole and without supertypes, which accepts exactly the same values.
        # Deciding which changes RAML reads as narrowing is the parser's rule.
        inherited = {wire: self._inherited(parents, wire) for wire in properties}
        if any(
            before is not None and before.render() != properties[wire].render() for wire, before in inherited.items()
        ):
            parents = []
            properties = self._properties(model, at, everything, output=output, known=properties)
        else:
            properties = {wire: prop for wire, prop in properties.items() if inherited[wire] is None}

        decl = TypeDecl(type=parents[0] if len(parents) == 1 else (parents or 'object'), properties=properties)
        if forbids_extra(model) and not (parents and any(forbids_extra(base) for base in supertypes(model))):
            decl.additional_properties = False
        if model.__doc__ and not is_default_doc(model):
            decl.description = model.__doc__.strip()
        return decl

    def _properties(
        self, model: type, at: str, names: set[str], *, output: Shape | None, known: Parameters | None = None
    ) -> Parameters:
        """The properties for the fields -- and, in output, computed fields -- named in `names`.

        In the model's order. A property already built is taken from `known`
        rather than built again, which would declare its named members twice.
        """
        known = known or {}
        out: Parameters = {}
        # Under `exclude_none` a `None` is left out: never null, and so optional.
        strip = output is not None and output.exclude_none
        for name, info in fields_of(model).items():
            if name not in names or (output is not None and info.exclude is True):
                continue
            wire = wire_name(name, info, output=output)
            if output is None:
                self._report_alias(name, info, wire, at)
            if wire is None:
                continue
            prop = known.get(wire)
            if prop is None:
                prop = self._optional(self.field(info, f'{at}.{name}', nullable=not strip), info, f'{at}.{name}')
                if strip and admits_none(info.annotation):
                    prop.required = False
            out[wire] = prop
        if output is None:
            return out
        for name, computed in computed_of(model).items():
            wire = (computed.alias or name) if output.by_alias else name
            if name not in names:
                continue
            prop = known.get(wire)
            if prop is None:
                returned = without_none(computed.return_type) if strip else computed.return_type
                prop = self.annotation(returned, f'{at}.{name}')
                if computed.description:
                    prop.description = computed.description
                # Always present in what the model writes, so required -- unless
                # it is a `None` left out.
                if strip and admits_none(computed.return_type):
                    prop.required = False
            out[wire] = prop
        return out

    @staticmethod
    def _wire(key: str, model: type, shape: Shape) -> str | None:
        """The key a field or computed field of `model` is written under, in `shape`."""
        fields = fields_of(model)
        if key in fields:
            return wire_name(key, fields[key], output=shape)
        computed = computed_of(model)[key]
        return (computed.alias or key) if shape.by_alias else key

    def _report_alias(self, name: str, info: FieldInfo, wire: str | None, at: str) -> None:
        """Say which keys a field is read from that its one RAML property does not name.

        The other `AliasChoices`, and an `AliasPath` into a nested value. Not
        the field's own name under `populate_by_name`: that setting is how a
        model is built by name in Python, and the alias is its wire key.
        """
        alias = info.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias] if alias is not None else []
        keys = [choice for choice in choices if isinstance(choice, str)]
        others = [key for key in dict.fromkeys(keys) if key != wire]
        paths = [f'the path {choice.path}' for choice in choices if isinstance(choice, AliasPath)]
        where = f'{at}.{name}'
        if wire is None:
            self.drop(where, 'read from a nested path, which no RAML property names; not described')
        elif others or paths:
            self.drop(where, f'read from {wire!r} and also from {", ".join([*others, *paths])}; RAML names one key')

    def _inherited(self, names: list[str], wire: str, seen: frozenset[str] = frozenset()) -> TypeDecl | None:
        """The property `wire` as the nearest of the types `names` declares it."""
        for name in names:
            decl = self.types.get(name)
            if decl is None or name in seen:
                continue
            if wire in decl.properties:
                return decl.properties[wire]
            found = self._inherited(_parents(decl), wire, seen | {name})
            if found is not None:
                return found
        return None

    def _diverges(self, model: type, shape: Shape) -> bool:
        """Does `model` -- or any model it reaches -- write, in `shape`, what it does not read?

        Assumed not while being decided, so a recursive model settles on what
        the rest of it says rather than looping.
        """
        known = self._diverging.get((model, shape))
        if known is not None:
            return known
        self._diverging[model, shape] = False
        verdict = bool(computed_of(model)) or any(
            info.exclude is True
            or wire_name(name, info, output=shape) != wire_name(name, info, output=None)
            or (shape.exclude_none and admits_none(info.annotation))
            or any(self._diverges(reached, shape) for reached in models_in(info.annotation))
            for name, info in fields_of(model).items()
        )
        verdict = verdict or any(self._diverges(base, shape) for base in supertypes(model))
        self._diverging[model, shape] = verdict
        return verdict

    # -- fields ---------------------------------------------------------------

    def field(self, info: FieldInfo, at: str, *, nullable: bool = True) -> TypeDecl:
        """One `FieldInfo` -> one declaration, constraints and all.

        What a value must look like, not whether it must be there: a model
        property's optionality is its own business (`_optional`), and a
        parameter's is `parameter`. `nullable=False` reads `X | None` as `X`,
        for a position whose value is never null on the wire.
        """
        annotation = info.annotation if nullable else without_none(info.annotation)
        decl = self.annotation(annotation, at, discriminator=self._tag_name((info, *info.metadata), at))
        self._constrain(decl, info.metadata, at)
        if info.title:
            decl.display_name = info.title
        if info.description:
            decl.description = info.description
        # Unnamed ones, and FastAPI's named `openapi_examples` on its own
        # `FieldInfo` subclasses, whose `value` is the example.
        examples = {f'e{index}': value for index, value in enumerate(info.examples or ())}
        named = getattr(info, 'openapi_examples', None) or {}
        examples.update({name: entry['value'] for name, entry in named.items() if 'value' in entry})
        written = {key: self._value(decl, value, at) for key, value in examples.items()}
        kept = {key: value for key, value in written.items() if not isinstance(value, Unset)}
        if kept:
            decl.examples = kept
        if info.deprecated:
            self.annotate(decl.annotations, 'deprecated', _deprecation(info.deprecated))
        return decl

    def parameter(self, info: FieldInfo, at: str) -> TypeDecl:
        """A query parameter, header or URI parameter: text or absent, never null.

        `None` in its annotation means only that it may be left out, and a
        default makes it `required: false` -- RAML's default in the position is
        `required: true`, and a document leaving that out would be stricter than
        the code.
        """
        return self._optional(self.field(info, at, nullable=False), info, at)

    def _optional(self, decl: TypeDecl, info: FieldInfo, at: str) -> TypeDecl:
        """Apply a field's optionality and its default to its declaration.

        **RAML's default in every position a field lands in is `required:
        true`.** A caller that reads the annotation and stops there renders an
        optional field as a mandatory one, and the document is then stricter
        than the code it describes -- wrong in the direction nothing complains
        about, because every request the tests send does carry the field.

        `default: ~` is not written for a field defaulting to `None`: RAML would
        take it as a value, and the field is simply absent.
        """
        if info.is_required():
            return decl
        decl.required = False
        if info.default is not None and info.default is not PydanticUndefined:
            decl.default = self._value(decl, info.default, at)
        return decl

    def _value(self, decl: TypeDecl, value: Any, at: str) -> Yaml | Unset:
        """A default or an example as the JSON value it stands for, or `UNSET` if RAML refuses it.

        RAML's `datetime` is RFC 3339 and requires an offset, which a naive
        `datetime` does not have -- pydantic accepts both under the one type.
        """
        members = {part.strip() for part in decl.type.split('|')} if isinstance(decl.type, str) else set()
        if isinstance(value, datetime.datetime) and value.tzinfo is None and 'datetime' in members:
            self.drop(at, f'{value.isoformat()} has no offset, which a RAML datetime requires; not written')
            return UNSET
        return as_yaml(value)

    def _tag_name(self, items: tuple[Any, ...], at: str) -> str | None:
        """The discriminator property `items` name, if they name one at all.

        Two spellings land in two places, which is why both are read:
        `Field(discriminator='kind')` sets `info.discriminator`, while
        `Annotated[Union[...], Discriminator('kind')]` leaves a `Discriminator`
        in `info.metadata`. Reading only the first renders the second as a plain
        union -- valid RAML that says less than the model does. `items` is
        either a field's pair of places, or the extras of an `Annotated` that
        pydantic did not lift into a field -- `list[Annotated[A | B, ...]]`.

        A `Discriminator` may instead wrap a **callable** that computes the tag.
        RAML names a property, so that has no spelling: the union stays an
        ordinary one, which is correct and less precise.
        """
        for item in items:
            inner = getattr(item, 'discriminator', None)
            # `Field(discriminator=Discriminator(...))`: a `FieldInfo` holding one.
            inner = getattr(inner, 'discriminator', inner)
            if isinstance(inner, str):
                return inner
            if inner is not None:
                self.drop(at, 'a callable discriminator has no RAML form; rendered as a plain union')
                return None
        return None

    # -- annotations ----------------------------------------------------------

    def annotation(self, annotation: Any, at: str, *, discriminator: str | None = None) -> TypeDecl:
        """One type annotation -> one declaration.

        An `Annotated` pydantic did not lift into a `FieldInfo` -- an array's
        item, a dict's value, a union member -- keeps its constraints and its
        discriminator here, and both are read from it rather than lost.
        """
        annotation, extra = unwrap_annotated(annotation)
        if not extra:
            return self._bare(annotation, at, discriminator=discriminator)
        # A `Field(...)` inside `Annotated` arrives as a whole `FieldInfo`.
        items = tuple(
            part for item in extra for part in ((*item.metadata, item) if isinstance(item, FieldInfo) else (item,))
        )
        if discriminator is None:
            discriminator = self._tag_name(items, at)
        decl = self._bare(annotation, at, discriminator=discriminator)
        self._constrain(decl, [item for item in items if not isinstance(item, (FieldInfo, Tag))], at)
        for item in items:
            if isinstance(item, FieldInfo) and item.description:
                decl.description = item.description
        return decl

    def _bare(  # noqa: PLR0911, PLR0912 - one return per kind reads better than nesting
        self, annotation: Any, at: str, *, discriminator: str | None
    ) -> TypeDecl:
        origin = get_origin(annotation)
        if is_union(annotation):
            args = get_args(annotation)
            if discriminator is not None:
                return self._unions.tagged(args, at, discriminator)
            return self._unions.plain(args, at)
        if origin is Literal:
            return literal_decl(get_args(annotation))
        if isinstance(annotation, typing.NewType):
            return self.annotation(annotation.__supertype__, at, discriminator=discriminator)
        if isinstance(annotation, typing.TypeAliasType):
            return self.annotation(annotation.__value__, at, discriminator=discriminator)
        if origin is None and annotation in SEQUENCES | MAPPINGS:
            origin = annotation  # a bare `list` or `dict`: of anything
        if origin in SEQUENCES:
            return self._sequence(annotation, origin, at)
        if origin in MAPPINGS:
            return self._mapping(get_args(annotation), at)
        if isinstance(annotation, type):
            if is_model(annotation):
                return TypeDecl(type=self.model(annotation))
            if issubclass(annotation, enum.Enum):
                return enum_decl(annotation)
            # Down the MRO, so the *most derived* match wins. `issubclass` in
            # table order would call a `bool` an integer, because Python makes
            # `bool` a subclass of `int` and no RAML author means that; the same
            # order keeps `datetime` from being read as its base `date`.
            for base in annotation.__mro__:
                spelling = self.scalars.get(base) or SCALARS.get(base)
                if spelling is not None:
                    return TypeDecl(type=spelling)
        if annotation in SCALARS:
            return TypeDecl(type=SCALARS[annotation])
        if annotation is None:
            return TypeDecl(type='nil')

        self.drop(at, f'no RAML spelling for {annotation!r}; rendered as any')
        return TypeDecl(type='any')

    def _sequence(self, annotation: Any, origin: Any, at: str) -> TypeDecl:
        args = get_args(annotation)
        if origin is tuple and args and not (len(args) == 2 and args[1] is Ellipsis):  # noqa: PLR2004 - `tuple[X, ...]`
            self.drop(at, 'a fixed-length tuple has no RAML form; rendered as an array')
        items = self.annotation(args[0], f'{at}[]') if args else TypeDecl(type='any')
        unique = True if origin in SETS else None
        # `string[]` where the item has a plain spelling, which is what a union
        # member and a nested `items` both need.
        if isinstance(items.type, str) and items.render() == items.type and '|' not in items.type:
            return TypeDecl(type=f'{items.type}[]', unique_items=unique)
        return TypeDecl(type='array', items=items, unique_items=unique)

    def _mapping(self, args: tuple[Any, ...], at: str) -> TypeDecl:
        """`dict[K, V]` -> RAML's pattern-any property, narrowed by the key type.

        JSON Schema has one `additionalProperties` and no place for `K`, so this
        is one of the things reading the model recovers. Only the values of
        such keys are typed: a RAML pattern property leaves the keys it does not
        match open (RAML 1.0 § Property Declarations), and
        `additionalProperties: false` may not stand beside it, so a key of
        another type is accepted, and reported as such.
        """
        key, value = (*args, Any, Any)[:2]
        key, _ = unwrap_annotated(key)
        pattern = ANY_KEY if key in (str, Any) else KEY_PATTERNS.get(key)
        if pattern is None:
            self.drop(at, f'no RAML property pattern for key type {key!r}; any key accepted')
            pattern = ANY_KEY
        elif pattern != ANY_KEY:
            self.drop(at, f'RAML cannot refuse a key that is not {key.__name__}; any key accepted')
        return TypeDecl(type='object', properties={pattern: self.annotation(value, f'{at}.{pattern}')})

    def _constrain(self, decl: TypeDecl, metadata: Any, at: str) -> None:
        """Apply constraints to a declaration, member by member if it is a union.

        A union holds no facets of its own -- `{type: string | nil, maxLength:
        3}` does not parse -- and pydantic applies them to each member that can
        take them. So does this: each member that picks one up is declared by
        name.
        """
        metadata = list(expanded(metadata))
        spelling = decl.type if isinstance(decl.type, str) else ''
        if '|' not in spelling:
            facets.apply(decl, metadata, at, self.drop)
            return
        if not metadata:
            return
        members = []
        for member in (part.strip() for part in spelling.split('|')):
            if member == 'nil':
                members.append(member)
                continue
            constrained = TypeDecl(type=member)
            facets.apply(constrained, metadata, at, self.drop)
            members.append(self._unions.spelled(constrained, at))
        decl.type = ' | '.join(members)


def _deprecation(deprecated: Any) -> str | None:
    """What a deprecation says to use instead: its message, or nothing for a bare `True`."""
    if isinstance(deprecated, str):
        return deprecated
    return getattr(deprecated, 'message', None)


def _parents(decl: TypeDecl) -> list[str]:
    """The names `decl` inherits from: one, several, or none for a union or no type."""
    if isinstance(decl.type, list):
        return list(decl.type)
    if isinstance(decl.type, str) and '|' not in decl.type:
        return [decl.type]
    return []
