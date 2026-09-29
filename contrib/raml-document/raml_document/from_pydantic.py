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

**A model has two shapes when it serialises differently from how it
validates**: a `serialization_alias`, an `exclude=True` field, a
`computed_field`. Inside `Walk.output()` such a model is declared a second time
as `{Name}Output`, which is what a response body refers to; a model that reads
and writes alike is declared once and shared.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import re
import types
import typing
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, get_args, get_origin

import annotated_types
from pydantic import BaseModel, Tag
from pydantic.fields import FieldInfo
from pydantic_core import PydanticSerializationError, to_jsonable_python

from raml_document.model import UNSET, Parameters, TypeDecl, Unset, Yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

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

#: What a RAML type name may not contain. `Page[Book]`, the `__name__` pydantic
#: gives a parametrised generic, is a type *expression* to a RAML parser -- an
#: array suffix it then fails to read -- so the brackets become underscores, the
#: spelling pydantic's own JSON Schema uses for the same class.
_NOT_NAME: Final = re.compile(r'[^A-Za-z0-9_]')


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
    #: (model, output?) -> the RAML name it was registered under, which is not
    #: always `__name__`: two models may share one.
    _names: dict[tuple[type, bool], str] = field(default_factory=dict)
    #: (discriminator, tag type) -> the synthesised base every union tagged
    #: that way shares.
    _bases: dict[tuple[str, str], str] = field(default_factory=dict)
    #: member name -> the discriminator it has been tagged by.
    _tagged: dict[str, str] = field(default_factory=dict)
    _diverging: dict[type, bool] = field(default_factory=dict)
    _output: bool = False

    def drop(self, at: str, what: str) -> None:
        message = f'{at}: {what}'
        if message not in self.dropped:
            self.dropped.append(message)

    @contextmanager
    def output(self) -> Iterator[None]:
        """Walk what models *serialise to* inside this block, not what they accept.

        For a response body. A model whose two shapes differ is declared again
        as `{Name}Output`; one whose shapes agree keeps its single declaration.
        """
        previous, self._output = self._output, True
        try:
            yield
        finally:
            self._output = previous

    # -- models ---------------------------------------------------------------

    def model(self, model: type[BaseModel]) -> str:
        """Register `model` and every model it reaches; return its RAML name."""
        output = self._output and self._diverges(model)
        known = self._names.get((model, output))
        if known is not None:
            return known
        name = self._name_for(model, output=output)
        self._names[model, output] = name
        # Reserved before the body is walked, so a self-reference finds it.
        self.types[name] = TypeDecl(type='object')
        self.types[name] = self._body(model, name, output=output)
        return name

    def _name_for(self, model: type[BaseModel], *, output: bool) -> str:
        """`__name__`, qualified by module where two models would collide.

        Qualified by the last module segment first and the whole module path
        after, because two packages that each keep a `User` in `models.py` agree
        on the last segment. A number is the last resort -- two classes built by
        one factory share even their module -- and every step past the plain
        name is reported, since a reader looks for the class by its name.
        """
        suffix = 'Output' if output else ''
        name = f'{_NOT_NAME.sub("_", model.__name__)}{suffix}'
        if name not in self.types:
            return name
        module = model.__module__
        for qualifier in (module.rsplit('.', 1)[-1], module):
            qualified = _NOT_NAME.sub('_', f'{qualifier}_{name}')
            if qualified not in self.types:
                break
        else:
            qualified = self.unique(qualified)
        self.drop(name, f'a second model of this name is declared as {qualified}')
        return qualified

    def unique(self, name: str) -> str:
        """`name`, or `name_2`, `name_3`... -- the first `types` does not hold."""
        candidate, number = name, 1
        while candidate in self.types:
            number += 1
            candidate = f'{name}_{number}'
        return candidate

    def _body(self, model: type[BaseModel], at: str, *, output: bool) -> TypeDecl:
        root = model.model_fields.get('root')
        if root is not None and len(model.model_fields) == 1:
            # A RootModel: the declaration *is* its single field.
            return self.field(root, f'{at}.root')

        parents = [self.model(base) for base in _supertypes(model)]
        everything = {*model.model_fields, *model.model_computed_fields}
        # Only what this class declares. RAML inherits the rest, so repeating an
        # inherited property would state twice what the supertype already says.
        own = {*_own_fields(model), *(set(model.model_computed_fields) & set(vars(model)))} if parents else everything
        properties = self._properties(model, at, own, output=output)
        clash = next((wire for wire, prop in properties.items() if not self._keeps(parents, wire, prop)), None)
        if clash is not None:
            # Python lets a subclass retype a field however it likes; RAML reads
            # a redeclared property as a narrowing of the inherited one and
            # refuses the document where it is not. Declared whole instead.
            self.drop(
                at,
                f'{clash!r} is redeclared as a type that does not narrow the inherited one; '
                'declared without its supertypes',
            )
            parents = []
            properties = self._properties(model, at, everything, output=output, known=properties)

        decl = TypeDecl(type=parents[0] if len(parents) == 1 else (parents or 'object'), properties=properties)
        if _forbids_extra(model) and not (parents and any(_forbids_extra(base) for base in _supertypes(model))):
            decl.additional_properties = False
        if model.__doc__ and not _is_default_doc(model):
            decl.description = model.__doc__.strip()
        return decl

    def _properties(
        self, model: type[BaseModel], at: str, names: set[str], *, output: bool, known: Parameters | None = None
    ) -> Parameters:
        """The properties for the fields -- and, in output, computed fields -- named in `names`.

        In the model's order. A property already built is taken from `known`
        rather than built again, which would declare its named members twice.
        """
        known = known or {}
        out: Parameters = {}
        for name, info in model.model_fields.items():
            if name not in names or (output and info.exclude is True):
                continue
            wire = _wire_name(name, info, output=output)
            out[wire] = known.get(wire) or self.optional(self.field(info, f'{at}.{name}'), info, f'{at}.{name}')
        if output:
            for name, computed in model.model_computed_fields.items():
                wire = computed.alias or name
                if name not in names:
                    continue
                if wire in known:
                    out[wire] = known[wire]
                    continue
                # Always present in what the model writes, so required.
                prop = self.annotation(computed.return_type, f'{at}.{name}')
                if computed.description:
                    prop.description = computed.description
                out[wire] = prop
        return out

    # -- redeclared properties --------------------------------------------------

    def _keeps(self, parents: list[str], wire: str, prop: TypeDecl) -> bool:
        """Does `prop` narrow what `parents` already declare under `wire`, if they declare it?"""
        inherited = self._inherited(parents, wire)
        return inherited is None or self._narrows(prop, inherited)

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

    def _narrows(self, child: TypeDecl, parent: TypeDecl) -> bool:
        """Will RAML read `child` as a restriction of `parent`, and not a stricter document than the model?

        Deliberately conservative: a case this answers wrongly `False` only
        costs the subtype its supertype. RAML refuses a required property
        made optional and a change of kind, and a facet the child does not
        restate is inherited -- which would hold the subclass to a bound its
        own field does not have.
        """
        if parent.required is None and child.required is False:
            return False
        facets, child_facets = _facets(parent), _facets(child)
        parent_enum, child_enum = facets.pop('enum', None), child_facets.pop('enum', None)
        if any(child_facets.get(key) != value for key, value in facets.items()):
            return False
        if parent_enum is not None and not (
            isinstance(parent_enum, list)
            and isinstance(child_enum, list)
            and all(value in parent_enum for value in child_enum)
        ):
            return False
        if not (isinstance(child.type, str) and isinstance(parent.type, str)):
            return child.type == parent.type
        if child.items is not None or parent.items is not None:
            return (
                child.type == parent.type == 'array'
                and child.items is not None
                and parent.items is not None
                and self._narrows(child.items, parent.items)
            )
        wider = [part.strip() for part in parent.type.split('|')]
        return all(any(self._extends(part.strip(), target) for target in wider) for part in child.type.split('|'))

    def _extends(self, name: str, target: str) -> bool:
        """Is the type `name` the type `target`, or declared -- through `types` -- as one of its kind?"""
        if target in (name, 'any'):
            return True
        if name.endswith('[]') and target.endswith('[]'):
            return self._extends(name[:-2], target[:-2])
        decl = self.types.get(name)
        if decl is None:
            return False
        return any(self._extends(parent, target) for parent in _parents(decl) if parent != name)

    def _diverges(self, model: type[BaseModel]) -> bool:
        """Does `model` -- or any model it reaches -- write a shape it does not read?

        Assumed not while being decided, so a recursive model settles on what
        the rest of it says rather than looping.
        """
        known = self._diverging.get(model)
        if known is not None:
            return known
        self._diverging[model] = False
        verdict = bool(model.model_computed_fields) or any(
            info.exclude is True
            or _wire_name(name, info, output=True) != _wire_name(name, info, output=False)
            or any(self._diverges(reached) for reached in _models_in(info.annotation))
            for name, info in model.model_fields.items()
        )
        verdict = verdict or any(self._diverges(base) for base in _supertypes(model))
        self._diverging[model] = verdict
        return verdict

    # -- fields ---------------------------------------------------------------

    def field(self, info: FieldInfo, at: str, *, nullable: bool = True) -> TypeDecl:
        """One `FieldInfo` -> one declaration, constraints and all.

        `nullable=False` reads `X | None` as `X`, for a position whose value is
        never null on the wire -- a query parameter or a header is text or
        absent, and `None` there means only that it may be left out.
        """
        annotation = info.annotation if nullable else _without_none(info.annotation)
        decl = self.annotation(annotation, at, discriminator=self._tag_name((info, *info.metadata), at))
        self.constrain(decl, info.metadata, at)
        if info.description:
            decl.description = info.description
        if info.examples:
            examples = {f'e{index}': self._value(decl, value, at) for index, value in enumerate(info.examples)}
            kept = {key: value for key, value in examples.items() if not isinstance(value, Unset)}
            if kept:
                decl.examples = kept
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
        return _as_yaml(value)

    def optional(self, decl: TypeDecl, info: FieldInfo, at: str = '') -> TypeDecl:
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
        take it as a value, and the field is simply absent. `at` names the
        position in what is reported.
        """
        if info.is_required():
            return decl
        decl.required = False
        if info.default is not None and info.default is not PydanticUndefined:
            decl.default = self._value(decl, info.default, at)
        return decl

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

    def annotation(self, annotation: Any, at: str, *, discriminator: str | None = None) -> TypeDecl:
        """One type annotation -> one declaration.

        An `Annotated` pydantic did not lift into a `FieldInfo` -- an array's
        item, a dict's value, a union member -- keeps its constraints and its
        discriminator here, and both are read from it rather than lost.
        """
        annotation, extra = _unwrap_annotated(annotation)
        if not extra:
            return self._bare(annotation, at, discriminator=discriminator)
        # A `Field(...)` inside `Annotated` arrives as a whole `FieldInfo`.
        items = tuple(
            part for item in extra for part in ((*item.metadata, item) if isinstance(item, FieldInfo) else (item,))
        )
        if discriminator is None:
            discriminator = self._tag_name(items, at)
        decl = self._bare(annotation, at, discriminator=discriminator)
        self.constrain(decl, [item for item in items if not isinstance(item, (FieldInfo, Tag))], at)
        for item in items:
            if isinstance(item, FieldInfo) and item.description:
                decl.description = item.description
        return decl

    def _bare(  # noqa: PLR0911 - one return per kind reads better than nesting
        self, annotation: Any, at: str, *, discriminator: str | None
    ) -> TypeDecl:
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
                spelling = self.scalars.get(base) or SCALARS.get(base)
                if spelling is not None:
                    return TypeDecl(type=spelling)
        if annotation in SCALARS:
            return TypeDecl(type=SCALARS[annotation])
        if annotation is None:
            return TypeDecl(type='nil')

        self.drop(at, f'no RAML spelling for {annotation!r}; rendered as any')
        return TypeDecl(type='any')

    def union(self, args: tuple[Any, ...], at: str, *, discriminator: str | None) -> TypeDecl:
        if discriminator is not None:
            return self.tagged_union(args, at, discriminator)
        members = [self.annotation(arg, at) for arg in args]
        # A list type is multiple inheritance, which has no place in a `|`
        # expression -- so it fails the same test as a member with no type.
        if not all(isinstance(member.type, str) for member in members):
            self.drop(at, 'a union member has no type expression; rendered as any')
            return TypeDecl(type='any')
        return TypeDecl(type=' | '.join(dict.fromkeys(self._spelled(member, at) for member in members)))

    def _spelled(self, decl: TypeDecl, at: str) -> str:
        """`decl` as a name a type expression can hold.

        A `|` joins names, and a member that carries facets -- `Literal['a', 'b']`
        is `string` with an `enum`, `constr(max_length=3)` is `string` with a
        `maxLength` -- has none. Joining its bare `type` would keep the member
        and silently lose what narrows it, so it is declared under a name of its
        own and the expression refers to that.
        """
        assert isinstance(decl.type, str)  # noqa: S101 - the caller checked
        if decl.render() == decl.type:
            return decl.type
        name = self.unique(_NOT_NAME.sub('_', at).strip('_') or 'Member')
        self.types[name] = decl
        return name

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
            if spelling is not None and isinstance(spelling.type, str):
                tag_type = spelling.type

        # RAML gives a type one `discriminatorValue`, so a member can answer to
        # one tag property. A second union selecting it by another is still a
        # union -- of the same members, without the selector.
        for name, _ in members:
            tagged_by = self._tagged.get(name)
            if tagged_by is not None and tagged_by != discriminator:
                self.drop(
                    at,
                    f'member {name} is already selected by {tagged_by!r}, and a RAML type has one '
                    f'discriminatorValue; rendered as a plain union',
                )
                return TypeDecl(type=' | '.join(name for name, _ in members))

        # One base per tag property and tag type, shared by every union that
        # selects that way. A base per *use* would give a member that sits in
        # two unions two bases naming one discriminator.
        key = (discriminator, tag_type or 'string')
        base = self._bases.get(key)
        if base is None:
            stem = re.sub(r'[^A-Za-z0-9]', '', at.rsplit('.', 1)[-1]) or 'Union'
            base = self.unique(f'{stem[:1].upper()}{stem[1:]}Base')
            self._bases[key] = base
            self.types[base] = TypeDecl(
                type='object',
                discriminator=discriminator,
                properties={discriminator: TypeDecl(type=key[1])},
            )
        for name, value in members:
            self._tagged[name] = discriminator
            member = self.types[name]
            # Added, not assigned: a member may already inherit a real model,
            # and overwriting would drop that supertype without a word.
            member.type = _inherit(member.type, base)
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
        if isinstance(items.type, str) and items.render() == items.type and '|' not in items.type:
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
        metadata = list(_expanded(metadata))
        spelling = decl.type if isinstance(decl.type, str) else ''
        if '|' in spelling and metadata:
            # A union holds no facets of its own -- `{type: string | nil,
            # maxLength: 3}` does not parse -- and pydantic applies them to each
            # member that can take them. So does this: each member that picks
            # one up is declared by name.
            members = [
                member if member == 'nil' else self._spelled(self._constrained(member, metadata, at), at)
                for member in (part.strip() for part in spelling.split('|'))
            ]
            decl.type = ' | '.join(members)
            return
        sequence = spelling == 'array' or spelling.endswith('[]')
        for item in metadata:
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

    def _constrained(self, spelling: str, metadata: Any, at: str) -> TypeDecl:
        decl = TypeDecl(type=spelling)
        self.constrain(decl, metadata, at)
        return decl

    def _general(self, decl: TypeDecl, item: Any, at: str) -> None:
        """Pydantic's own metadata objects, which carry several fields at once.

        Each setting is read or reported by name: `constr(to_upper=True)`
        carries a `maxLength` RAML has and a transformation it has not, and
        naming the whole object would hide the first.
        """
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
            # A bound either way, and never looser than one already stated.
            bound = float(decimal.Decimal(10) ** (digits - places) - decimal.Decimal(1).scaleb(-places))
            decl.maximum = bound if decl.maximum is None else min(decl.maximum, bound)
            decl.minimum = -bound if decl.minimum is None else max(decl.minimum, -bound)
        elif digits is not None:
            self.drop(at, f'max_digits={digits} without decimal_places has no RAML facet; not written')
        read = {'pattern', 'decimal_places', 'max_digits'}
        rest = {key: value for key, value in getattr(item, '__dict__', {}).items() if value is not None}
        for key, value in rest.items():
            if key not in read:
                self.drop(at, f'{key}={value!r} has no RAML facet; not written')
        if not rest and pattern is None and places is None and digits is None:
            self.drop(at, f'no RAML facet for {item!r}')


def _inherit(existing: str | list[str] | None, added: str) -> str | list[str]:
    """Add a supertype to whatever a declaration already inherits.

    `object` is the absence of a supertype rather than one of them, so it is
    replaced; a real name is kept and the two become multiple inheritance.
    """
    if existing is None or existing in ('object', added):
        return added
    current = [existing] if isinstance(existing, str) else list(existing)
    return current if added in current else [*current, added]


#: What a rendered declaration says that is not a restriction of its values.
_NOT_FACETS: Final = frozenset({'type', 'displayName', 'description', 'required', 'default', 'examples', 'items'})


def _facets(decl: TypeDecl) -> dict[str, Yaml]:
    """What `decl` restricts its values by -- `enum`, `maxLength`, inline `properties` -- by RAML key."""
    rendered = decl.render()
    if not isinstance(rendered, dict):
        return {}
    return {key: value for key, value in rendered.items() if key not in _NOT_FACETS}


def _parents(decl: TypeDecl) -> list[str]:
    """The names `decl` inherits from: one, several, or none for a union or no type."""
    if isinstance(decl.type, list):
        return list(decl.type)
    if isinstance(decl.type, str) and '|' not in decl.type:
        return [decl.type]
    return []


def _expanded(metadata: Any) -> Iterator[Any]:
    """`metadata` with each group opened up and each placeholder left out.

    `constr(...)` arrives as one `StringConstraints` and `conint(...)` as one
    `Interval`, each a group of the `MinLen`, `Ge`... it stands for; the `con*`
    helpers also leave a `None` for every constraint not given.
    """
    for item in metadata or ():
        if isinstance(item, annotated_types.GroupedMetadata):
            yield from _expanded(item)
        elif item is not None:
            yield item


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


def _wire_name(name: str, info: FieldInfo, *, output: bool) -> str:
    """The key a field travels under: read by its validation alias, written by its serialization one."""
    chosen = info.serialization_alias if output else info.validation_alias
    if isinstance(chosen, str):
        return chosen
    return info.alias or name


def _without_none(annotation: Any) -> Any:
    """`X | None` -> `X`; `X | Y | None` -> `X | Y`; anything else unchanged."""
    if get_origin(annotation) not in (typing.Union, types.UnionType):
        return annotation
    kept = tuple(arg for arg in get_args(annotation) if arg is not type(None))
    if not kept or len(kept) == len(get_args(annotation)):
        return annotation
    return kept[0] if len(kept) == 1 else typing.Union[kept]  # noqa: UP007 - built from a tuple


def _models_in(annotation: Any) -> Iterator[type[BaseModel]]:
    """Every model an annotation names, however deeply it is nested."""
    annotation, _ = _unwrap_annotated(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _models_in(arg)


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


def _as_yaml(value: Any) -> Yaml:  # noqa: PLR0911 - one return per kind
    """A Python value as the JSON value it travels as, in a form `yaml.safe_dump` writes.

    Not `str()`: a `datetime` would read `2024-01-01 12:00:00`, which is not
    RFC 3339, and a model `x=1`, which is not an object. A `Decimal` is a
    number here, where pydantic's JSON mode would write a string, because
    the declaration it lands under is a RAML `number`.
    """
    if isinstance(value, enum.Enum):
        return _as_yaml(value.value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_as_yaml(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_yaml(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        return _as_yaml(value.model_dump(by_alias=True))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _as_yaml(dataclasses.asdict(value))
    try:
        # A date, a duration, a UUID, a URL: what pydantic writes for each.
        return _as_yaml(to_jsonable_python(value))
    except PydanticSerializationError:
        return str(value)


def _supertypes(model: type[BaseModel]) -> list[type[BaseModel]]:
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


def _own_fields(model: type[BaseModel]) -> set[str]:
    """The fields declared on this class, not the ones it inherits.

    An override re-annotates, so a narrowed property is its own and is written
    again -- which is what RAML expects of a subtype that restricts one.
    """
    return set(getattr(model, '__annotations__', {})) & set(model.model_fields)


def _forbids_extra(model: type[BaseModel]) -> bool:
    return bool(getattr(model, 'model_config', {}).get('extra') == 'forbid')


def _is_default_doc(model: type[BaseModel]) -> bool:
    """Is `__doc__` the one pydantic generates for a model with no docstring?"""
    return (model.__doc__ or '').startswith(f'{model.__name__}(')


try:  # pragma: no cover - the name moved between pydantic releases
    from pydantic_core import PydanticUndefined
except ImportError:  # pragma: no cover
    from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined, no-redef]
