"""The effective view of one declaration — docs/16-graph.md § 9.

Answers the question a reader asks most often and that nothing else here
answers: **what is this type, actually?** Every inherited property in one place,
every constraint beside the property it constrains, and for each one the file
and line it was really written on.

Output is RAML-shaped on purpose. It is the notation the reader already knows,
it pastes back into a document, and two versions of it diff. Origin rides in
trailing comments so the result stays valid YAML.

This walks the **model**, not the graph. The projection carries what a traversal
needs and deliberately drops facet detail (docs/16 § 2.5), so rendering from it
would be rendering from a lossy copy. `Graph.find` locates the declaration and
`Graph.shape_at` hands back the shape; everything below reads that shape.

Requires `ParseOptions(unwrap=True)`. Without it the properties have not been
merged, and this shows the declaration rather than the type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from pyraml.types.base import ScalarFacet
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyraml.types.base import BaseShape, Property

__all__ = ['render']

#: A property name that needs no quoting as a YAML key. Deliberately narrow:
#: quoting something that did not need it is harmless, and the reverse is not.
_PLAIN_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]*\??')

#: Facets whose RAML spelling is not just the camel case of the slot name.
_SPELLINGS = {'multiple_of': 'multipleOf', 'unique_items': 'uniqueItems', 'file_types': 'fileTypes'}

#: Never rendered as a facet: printed by the caller, or structure rather than
#: constraint.
_NOT_A_FACET = frozenset({'any_of', 'base', 'id', 'items', 'name', 'pattern_properties', 'properties', 'raml', 'type'})


@dataclass(frozen=True, slots=True)
class _Line:
    """One rendered line and its trailing note, kept apart until the end.

    Separate so the notes can be aligned into a column, which is the whole
    reason this view is easier to read than the source it came from. Joining
    them earlier would mean finding the comment again by searching for `#`,
    and `#` occurs inside patterns and descriptions.
    """

    text: str
    note: str = ''


@dataclass(frozen=True, slots=True)
class _Level:
    """Where the walk is: how much further to expand, and how far to indent.

    One object because the four travel together through every function here,
    and threading them separately is how an `indent` and a `seen` get out of
    step on one branch.
    """

    depth: int
    root: str
    indent: str
    #: Shapes already open further up. A type cycle is a cycle in the model by
    #: design (docs/07 § 4), so the walk has to be finite by construction.
    seen: frozenset[int]

    def inside(self, base: BaseShape, *, extra: str = '  ') -> _Level:
        return replace(self, depth=self.depth - 1, indent=self.indent + extra, seen=self.seen | {base.id})

    def opens(self, base: BaseShape) -> bool:
        """Whether to expand `base` in place rather than name it.

        Depth alone is not enough: opening a scalar produces `level:` followed
        by `type: integer`, which is two lines saying what one line said. Only
        something with structure is worth the indent.
        """
        return self.depth > 1 and base.id not in self.seen and _has_structure(base)


def render(base: BaseShape, *, depth: int = 1, root: str = '') -> Iterator[str]:
    """The declaration as RAML, one line at a time.

    `depth` counts levels of *expansion*: 1 shows this type's own effective
    properties and names their types without opening them; 2 opens one more
    level. A named type is worth naming rather than inlining — the reader can
    ask for it by name — so the default stops at 1.

    `root` is the directory that the paths in the comments are relative to.

    Buffers rather than streams, because the notes are aligned into a column
    and the column width is not known until the last line is in hand. A type
    is small; the whole point is that a person reads the result.
    """
    level = _Level(depth=depth, root=root, indent='  ', seen=frozenset({base.id}))
    lines = [_Line(f'{base.name or "<anonymous>"}:', _where(base, root)), *_body(base, level)]
    width = max((len(line.text) for line in lines if line.note), default=0)
    for line in lines:
        yield f'{line.text:<{width}}  # {line.note}' if line.note else line.text


def _body(base: BaseShape, level: _Level) -> Iterator[_Line]:
    shape = base.shape
    yield _Line(f'{level.indent}type: {_type_name(base)}')
    if base.inherits:
        parents = ', '.join(parent.name or '<anonymous>' for parent in base.inherits)
        yield _Line(f'{level.indent}inherits: [{parents}]')
    yield from _facets(base, level.indent)

    if isinstance(shape, ObjectShape):
        yield from _properties(base, shape, level)
    elif isinstance(shape, ArrayShape) and shape.items is not None:
        yield from _member(shape.items, 'items', level)
    elif isinstance(shape, UnionShape) and shape.any_of:
        yield _Line(f'{level.indent}anyOf:')
        for member in shape.any_of:
            yield _Line(f'{level.indent}  - {_type_name(member)}')


def _properties(base: BaseShape, shape: ObjectShape, level: _Level) -> Iterator[_Line]:
    """Every effective property, each tagged with where it was really written."""
    properties = shape.properties or {}
    patterns = shape.pattern_properties or {}
    if not properties and not patterns:
        return
    yield _Line(f'{level.indent}properties:')
    for name, prop in properties.items():
        key = name if prop.required else f'{name}?'
        yield from _one(key, prop.base, _origin(base, name, prop), level)
    for pattern in patterns.values():
        # Rendered back in its `/regex/` form. The model keys these by the
        # stripped pattern, and the spec's `//:` — constrain *every* additional
        # property — strips to nothing, so the name alone is an empty key.
        yield from _one(f'/{pattern.pattern.pattern}/', pattern.base, None, level)


def _one(name: str, base: BaseShape, origin: str | None, level: _Level) -> Iterator[_Line]:
    """One property: short form when nothing but its type needs saying."""
    inner = replace(level, indent=level.indent + '  ')
    marker = 'recursive' if isinstance(base.shape, RecursiveShape) else None
    note = ', '.join(part for part in (marker, origin, _where(base, level.root)) if part)
    key = _key(name)

    if inner.opens(base) and marker is None:
        yield _Line(f'{inner.indent}{key}:', note)
        yield from _body(base, inner.inside(base))
        return
    facets = list(_facets(base, inner.indent + '  '))
    if not facets:
        yield _Line(f'{inner.indent}{key}: {_type_name(base)}', note)
        return
    yield _Line(f'{inner.indent}{key}:', note)
    yield _Line(f'{inner.indent}  type: {_type_name(base)}')
    yield from facets


def _key(name: str) -> str:
    """A property name as a YAML key, quoted when it would not parse plain.

    Pattern properties force this. `/^x-/` is fine bare, but `//` renders as an
    empty key once the slashes are the only content, and an empty plain key is
    not valid YAML — which broke three corpus fixtures and, with them, this
    module's claim that its output pastes back.
    """
    return name if _PLAIN_KEY.fullmatch(name) else "'" + name.replace("'", "''") + "'"


def _member(base: BaseShape, key: str, level: _Level) -> Iterator[_Line]:
    if level.opens(base) and not isinstance(base.shape, RecursiveShape):
        yield _Line(f'{level.indent}{key}:')
        yield from _body(base, level.inside(base))
    else:
        yield _Line(f'{level.indent}{key}: {_type_name(base)}')


# -- reading the model --------------------------------------------------------


def _has_structure(base: BaseShape) -> bool:
    """Whether this type contains anything an extra level would reveal."""
    shape = base.shape
    if isinstance(shape, ObjectShape):
        return bool(shape.properties or shape.pattern_properties)
    if isinstance(shape, ArrayShape):
        return shape.items is not None
    if isinstance(shape, UnionShape):
        return bool(shape.any_of)
    return False


def _type_name(base: BaseShape) -> str:
    """What to call this type in one word.

    `alias` first, and that is not a detail: `address: Address` and
    `UserList: User[]` both put an *alias* of the referenced type in place
    rather than the declaration (docs/07 § 3.6). Reading `type` instead prints
    `object` for both — true, and useless.

    A recursion marker names the type it closes back to. Its own `type` is
    `recursive`, which tells the reader nothing about which cycle they are in.
    """
    if isinstance(base.shape, RecursiveShape):
        return base.shape.head.name or 'recursive'
    if base.alias is not None and base.alias.name:
        return base.alias.name
    if len(base.inherits) == 1 and base.inherits[0].name:
        return base.inherits[0].name
    return base.type or 'any'


def _origin(owner: BaseShape, name: str, prop: Property) -> str | None:
    """Which ancestor really declared this property, or `None` for `owner` itself.

    The **furthest** ancestor that declares it at the same position, not the
    nearest: after unwrap a parent carries its own parents' properties too, so
    `Admin.id` is found on `User` as well as on `Entity`, and `Entity` is the
    answer a reader wants.

    Position is what identifies "the same" property. A subtype that *narrows*
    an inherited property re-declares it at its own line, so the position stops
    matching and the subtype is correctly reported as the origin — which is
    exactly the case a reader is trying to resolve.
    """
    where = (prop.base.location, prop.base.key_pos)
    found: str | None = None
    frontier = list(owner.inherits)
    while frontier:
        parent = frontier.pop(0)
        inherited = getattr(parent.shape, 'properties', None) or {}
        candidate = inherited.get(name)
        if candidate is not None and (candidate.base.location, candidate.base.key_pos) == where:
            found = parent.name or found
            frontier.extend(parent.inherits)
    return found


def _where(base: BaseShape, root: str) -> str:
    """`path:line`, relative to the entry document's directory.

    The whole relative path, not the bare file name: a project with
    `lib/a/common.raml` and `lib/b/common.raml` is exactly the kind that needs
    this view, and printing `common.raml` for both would point at neither.
    """
    if not base.key_pos.is_known:
        return ''
    return f'{base.location.removeprefix(root) or base.location}:{base.key_pos.line}'


def _facets(base: BaseShape, indent: str) -> Iterator[_Line]:
    """Every constraint the kind holds, in RAML spelling.

    Read off `__slots__` rather than from a table, for the reason the golden
    projector does (docs/14 § 2): a facet added to a kind has to show up here
    without this module being edited, or the view silently omits a constraint.
    """
    shape = base.shape
    if shape is not None:
        for slot in _slots(type(shape)):
            if slot in _NOT_A_FACET:
                continue
            value = getattr(shape, slot, None)
            if isinstance(value, ScalarFacet):
                yield _Line(f'{indent}{_SPELLINGS.get(slot, _camel(slot))}: {_scalar(value.value)}')
    if base.enum is not None:
        yield _Line(f'{indent}enum: [{", ".join(str(member.raw) for member in base.enum)}]')
    if base.description is not None and base.description.value:
        yield _Line(f'{indent}description: {base.description.value.strip().splitlines()[0]}')


def _slots(cls: type) -> Iterator[str]:
    for klass in cls.__mro__:
        yield from getattr(klass, '__slots__', ())


def _camel(name: str) -> str:
    head, _, rest = name.partition('_')
    return head + ''.join(part.title() for part in rest.split('_') if part)


def _scalar(value: Any) -> str:
    """A facet value as RAML would spell it.

    A `Fraction` is expanded exactly rather than divided: numbers never pass
    through `float` here either (docs/10 § 5.2), and `1.1` reaching a reader as
    `1.100000000000000088` would be this module's defect, not the parser's.
    """
    if value is True or value is False:
        return 'true' if value else 'false'
    if isinstance(value, Fraction):
        return _number(value)
    return str(value)


def _number(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    residue = value.denominator
    for factor in (2, 5):
        while residue % factor == 0:
            residue //= factor
    if residue != 1:
        return f'{value.numerator}/{value.denominator}'
    digits, scaled = 0, value
    while scaled.denominator != 1:
        scaled *= 10
        digits += 1
    text = str(abs(scaled.numerator)).rjust(digits + 1, '0')
    return f'{"-" if scaled.numerator < 0 else ""}{text[:-digits]}.{text[-digits:]}'
