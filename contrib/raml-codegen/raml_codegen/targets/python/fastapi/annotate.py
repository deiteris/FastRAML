"""The `python-fastapi` target's spellings: pydantic models, with the facets enforced.

The traversal is in `../shared/annotate.py`. What is here is the six hooks,
and the one thing this target does that the client target refuses to: a
`minLength:` becomes `min_length=`, so the generated server rejects a request
the document forbids.

That is not this package holding a rule of the language (docs/17 § 1). RAML says
what `minLength:` constrains and pydantic says the same thing in its own words;
transcribing one into the other is a spelling. What would be a rule is deciding
something the document does not say — and there is one of those, named below.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..shared.annotate import Annotation, Annotator

if TYPE_CHECKING:
    from ....naming import Names
    from ....reader import Tree
    from ....tree import Shape

__all__ = ['FastapiAnnotator', 'make_annotator']

#: The scalar kinds, and what each one is once pydantic has parsed it. No
#: conversion form: a generated model states the type and pydantic reads the
#: JSON into it, so there is no `.isoformat()` for a template to write.
_SCALARS: dict[str, Annotation] = {
    'any': Annotation('Any', imports=frozenset({'Any'})),
    'nil': Annotation('None'),
    'null': Annotation('None'),
    'boolean': Annotation('bool'),
    'string': Annotation('str'),
    'integer': Annotation('int'),
    'number': Annotation('float'),
    'file': Annotation('UploadFile', imports=frozenset({'UploadFile'})),
    'datetime': Annotation('datetime.datetime', imports=frozenset({'datetime'})),
    'datetime-only': Annotation('datetime.datetime', imports=frozenset({'datetime'})),
    'date-only': Annotation('datetime.date', imports=frozenset({'datetime'})),
    'time-only': Annotation('datetime.time', imports=frozenset({'datetime'})),
}

_ANY = Annotation('Any', imports=frozenset({'Any'}))
_MAPPING = Annotation('dict[str, Any]', imports=frozenset({'Any'}))

#: A scalar's facets, and what pydantic calls each.
#:
#: `multiple_of` is missing on purpose. pydantic compares in binary floating
#: point, so with `multipleOf: 1.1` it accepts `3.3000000000000003`, which the
#: exact decimal arithmetic the parser uses rejects. Spelling `Decimal` instead
#: would let a facet decide the *type*, which is the one thing a constraint must
#: not do. So it stays in the docstring, where the client target leaves all of
#: them.
_SCALAR_FACETS = (
    ('min_length', 'min_length'),
    ('max_length', 'max_length'),
    ('minimum', 'ge'),
    ('maximum', 'le'),
    ('pattern', 'pattern'),
)

#: An array's, which pydantic spells with the same two names a string uses.
_ARRAY_FACETS = (
    ('min_items', 'min_length'),
    ('max_items', 'max_length'),
)


class FastapiAnnotator(Annotator):
    """Shapes as pydantic models, with each facet as the constraint it is."""

    __slots__ = ()

    def scalar(self, kind: str, shape: Shape | None = None) -> Annotation:
        base = _SCALARS.get(kind, _ANY)
        return base if shape is None else _constrained(base, shape, _SCALAR_FACETS)

    def mapping(self) -> Annotation:
        return _MAPPING

    def enum(self, shape: Shape) -> Annotation:
        """Spell a closed set of values as a `Literal` rather than an `Enum`.

        RAML's `enum:` lists values and attaches no names to them. An `Enum`
        subclass would have to invent the names, and an identifier for
        `9780441013593` is guesswork a reader cannot check. pydantic validates a
        `Literal` and puts its members in the schema either way.
        """
        members = ', '.join(repr(value) for value in shape.get('enum', ()))
        return Annotation(f'Literal[{members}]', imports=frozenset({'Literal'}))

    def model(self, name: str) -> Annotation:
        return Annotation(name, models=frozenset({name}))

    def array(self, shape: Shape, item: Annotation) -> Annotation:
        base = Annotation(
            f'list[{item.spelling}]',
            imports=item.imports,
            models=item.models,
            runtime=item.runtime,
            bare=f'list[{item.plain}]',
        )
        return _constrained(base, shape, _ARRAY_FACETS, unique=bool(shape.get('unique_items')))

    def union(self, shape: Shape) -> Annotation:
        """Spell a union as `A | B`, tagged where the document tags it.

        pydantic tells the members apart itself, so unlike the client target
        there is nothing here to guess from required properties. Where every
        member states a `discriminatorValue:` under one `discriminator:`, the
        document has already said which property decides, and saying so makes
        the error message name the right member instead of listing them all.

        Only where every member states one. RAML defaults an unstated value to
        the type name; applying that default here would be a rule of the
        language living in a consumer (docs/17 § 1), and a tagged union missing
        one tag rejects valid payloads rather than merely reporting worse.
        """
        members = self.members(shape)
        if not members:
            return _ANY

        spelling = ' | '.join(dict.fromkeys(member.annotation.spelling for member in members))
        bare = ' | '.join(dict.fromkeys(member.annotation.plain for member in members))
        imports = frozenset[str]().union(*(member.annotation.imports for member in members))
        models = frozenset[str]().union(*(member.annotation.models for member in members))

        markers = {member.discriminator for member in members}
        tagged = (
            len(members) > 1
            and len(markers) == 1
            and None not in markers
            and all(member.discriminator_value is not None for member in members)
        )
        if not tagged:
            return Annotation(spelling, imports=imports, models=models, bare=bare)
        marker = next(iter(markers))
        return Annotation(
            f'Annotated[{spelling}, Field(discriminator={marker!r})]',
            imports=imports | {'Annotated', 'Field'},
            models=models,
            bare=bare,
        )


def make_annotator(tree: Tree, names: Names) -> Annotator:
    return FastapiAnnotator(tree=tree, names=names)


def _constrained(
    base: Annotation,
    shape: Shape,
    facets: tuple[tuple[str, str], ...],
    *,
    unique: bool = False,
) -> Annotation:
    """Wrap a spelling in the constraints the shape states, if it states any.

    `Annotated[str, Field(pattern=...)]` rather than a `Field(...)` default, so
    the constraint travels with the type: through an alias, into a list's item,
    and into a route parameter, which takes `Annotated` and not a default.
    """
    arguments = [f'{name}={_literal(key, shape[key])}' for key, name in facets if shape.get(key) is not None]  # type: ignore[literal-required]
    if not arguments and not unique:
        return base

    parts = [f'Field({", ".join(arguments)})'] if arguments else []
    if unique:
        parts.append('AfterValidator(unique_items)')
    imports = base.imports | {'Annotated'}
    if arguments:
        imports |= {'Field'}
    if unique:
        imports |= {'AfterValidator'}
    return replace(
        base,
        spelling=f'Annotated[{base.spelling}, {", ".join(parts)}]',
        bare=base.plain,
        imports=imports,
        runtime=(base.runtime | {'unique_items'}) if unique else base.runtime,
    )


def _literal(key: str, value: object) -> str:
    """One facet's value, as the Python literal that states it.

    A numeric bound arrives as the exact decimal text the document wrote
    (`ExactDecimal` is a string, docs/16 § 6). It is written out verbatim, so
    nothing here parses it into a float and back -- the generated source says
    what the document said.
    """
    if key == 'pattern':
        return _pattern(str(value))
    if key in {'minimum', 'maximum'}:
        return str(value)
    return repr(value)


def _pattern(text: str) -> str:
    r"""A regex as a source literal, raw where a raw string can hold it.

    `r'^\d{13}$'` reads as the author wrote it. A raw string cannot escape a
    quote and cannot end in a backslash, so those two fall back to `repr`, which
    doubles the backslashes but means the same regex.
    """
    if "'" not in text and not text.endswith('\\'):
        return f"r'{text}'"
    return repr(text)
