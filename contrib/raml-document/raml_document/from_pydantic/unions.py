"""Unions: plain ones as type expressions, discriminated ones as a base and its subtypes.

One `Unions` belongs to one `Walk`: the bases it synthesises are shared by
every union in the document that selects the same way, so they are state of
the whole traversal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from raml_document.from_pydantic.introspect import fields_of, is_model, literal_value, unwrap_annotated
from raml_document.from_pydantic.tables import type_name
from raml_document.model import UNSET, TypeDecl, Unset, Yaml

if TYPE_CHECKING:
    from raml_document.from_pydantic.walk import Walk

__all__ = ['Unions']


@dataclass(slots=True)
class Unions:
    walk: Walk
    #: (discriminator, tag type) -> the synthesised base every union tagged
    #: that way shares.
    _bases: dict[tuple[str, str], str] = field(default_factory=dict)
    #: member name -> the discriminator it has been tagged by.
    _tagged: dict[str, str] = field(default_factory=dict)

    def plain(self, args: tuple[Any, ...], at: str) -> TypeDecl:
        """`A | B` as a type expression of the members' names."""
        members = [self.walk.annotation(arg, at) for arg in args]
        # A list type is multiple inheritance, which has no place in a `|`
        # expression -- so it fails the same test as a member with no type.
        if not all(isinstance(member.type, str) for member in members):
            self.walk.drop(at, 'a union member has no type expression; rendered as any')
            return TypeDecl(type='any')
        return TypeDecl(type=' | '.join(dict.fromkeys(self.spelled(member, at) for member in members)))

    def spelled(self, decl: TypeDecl, at: str) -> str:
        """`decl` as a name a type expression can hold.

        A `|` joins names, and a member that carries facets -- `Literal['a', 'b']`
        is `string` with an `enum`, `constr(max_length=3)` is `string` with a
        `maxLength` -- has none. Joining its bare `type` would keep the member
        and silently lose what narrows it, so it is declared under a name of its
        own and the expression refers to that.
        """
        assert isinstance(decl.type, str)  # noqa: S101 - the callers checked
        if decl.render() == decl.type:
            return decl.type
        name = self.walk.unique(type_name(at).strip('_') or 'Member')
        self.walk.types[name] = decl
        return name

    def tagged(self, args: tuple[Any, ...], at: str, discriminator: str) -> TypeDecl:
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
        walk = self.walk
        members: list[tuple[str, Yaml]] = []
        tag_type: str | None = None
        for arg in args:
            model, _ = unwrap_annotated(arg)
            if not is_model(model):
                walk.drop(at, 'a discriminated union member is not a model')
                return TypeDecl(type='any')
            name = walk.model(model)
            tag = fields_of(model).get(discriminator)
            value = literal_value(tag.annotation) if tag is not None else UNSET
            if isinstance(value, Unset):
                walk.drop(at, f'member {name} has no Literal {discriminator!r} to identify it')
                return TypeDecl(type='any')
            members.append((name, value))
            spelling = walk.types[name].properties.get(discriminator)
            if spelling is not None and isinstance(spelling.type, str):
                tag_type = spelling.type
        expression = TypeDecl(type=' | '.join(name for name, _ in members))

        # RAML gives a type one `discriminatorValue`, so a member can answer to
        # one tag property. A second union selecting it by another is still a
        # union -- of the same members, without the selector.
        for name, _ in members:
            tagged_by = self._tagged.get(name)
            if tagged_by is not None and tagged_by != discriminator:
                walk.drop(
                    at,
                    f'member {name} is already selected by {tagged_by!r}, and a RAML type has one '
                    f'discriminatorValue; rendered as a plain union',
                )
                return expression

        base = self._base(discriminator, tag_type or 'string', at)
        for name, value in members:
            self._tagged[name] = discriminator
            member = walk.types[name]
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
        return expression

    def _base(self, discriminator: str, tag_type: str, at: str) -> str:
        """The base for unions selecting by `discriminator`, of `tag_type`, declared once.

        One base per tag property and tag type, shared by every union that
        selects that way. A base per *use* would give a member that sits in two
        unions two bases naming one discriminator.
        """
        key = (discriminator, tag_type)
        base = self._bases.get(key)
        if base is None:
            stem = re.sub(r'[^A-Za-z0-9]', '', at.rsplit('.', 1)[-1]) or 'Union'
            base = self.walk.unique(f'{stem[:1].upper()}{stem[1:]}Base')
            self._bases[key] = base
            self.walk.types[base] = TypeDecl(
                type='object', discriminator=discriminator, properties={discriminator: TypeDecl(type=tag_type)}
            )
        return base


def _inherit(existing: str | list[str] | None, added: str) -> str | list[str]:
    """Add a supertype to whatever a declaration already inherits.

    `object` is the absence of a supertype rather than one of them, so it is
    replaced; a real name is kept and the two become multiple inheritance.
    """
    if existing is None or existing in ('object', added):
        return added
    current = [existing] if isinstance(existing, str) else list(existing)
    return current if added in current else [*current, added]
