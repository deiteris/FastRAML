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
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import yaml

from pyraml.types.base import ScalarFacet
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from pyraml.types.jsonschema_ import projected

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyraml.parser.directives import SecurityScheme
    from pyraml.parser.endpoints import Body, EndPoint, Operation, Response
    from pyraml.parser.security import SecuritySchemeDescription
    from pyraml.positions import Position
    from pyraml.registry import Raml
    from pyraml.types.base import BaseShape, Property

__all__ = ['Sources', 'render', 'render_endpoint', 'render_operation']

#: Facets whose RAML spelling is not just the camel case of the slot name.
_SPELLINGS = {'multiple_of': 'multipleOf', 'unique_items': 'uniqueItems', 'file_types': 'fileTypes'}

#: Wide enough that PyYAML never folds a value onto a second line: a wrapped
#: scalar would break the one-value-per-line shape everything here assumes.
_UNWRAPPED = 1 << 30

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


@dataclass(frozen=True, slots=True)
class Sources:
    """Where each trait and resource type was declared, so a merged-in item can
    name the thing that contributed it.

    A trait's body is merged into the operation before anything is decoded, so
    a query parameter it supplied ends up on the operation carrying the
    **trait's** file and line. That is the provenance, and it is free; turning
    it back into a name needs the span the line falls in.

    The span is exact, not inferred: `key_pos.line` to `value_pos.end_line`,
    both of which the parser records. An earlier version guessed the end as
    "until the next declaration in the same file" and mis-attributed a method's
    own query parameter to the resource type declared above it, because the
    last declaration in a file has no next one to stop at.

    The narrowest containing span wins, so a nested declaration beats the
    enclosing one. Attribution is *also* gated on the site having applied the
    declaration — a confident wrong name is worse than none, and one bound
    checking the other is cheap.
    """

    #: file URI -> (start line, end line, name), narrowest first.
    spans: dict[str, list[tuple[int, int, str]]]

    @classmethod
    def of(cls, raml: Raml) -> Sources:
        spans: dict[str, list[tuple[int, int, str]]] = {}
        for location, fragment in raml.fragments.items():
            found: list[tuple[int, int, str]] = []
            for group in ('traits', 'resource_types', 'types', 'annotation_types', 'security_schemes'):
                for name, declared in (getattr(fragment, group, None) or {}).items():
                    start, end = getattr(declared, 'key_pos', None), getattr(declared, 'value_pos', None)
                    if start is not None and start.is_known and end is not None and end.end_line >= start.line:
                        found.append((start.line, end.end_line, name))
            if found:
                spans[location] = sorted(found, key=lambda span: (span[1] - span[0], span[0]))
        return cls(spans=spans)

    def containing(self, location: str, line: int) -> str | None:
        """The narrowest declaration whose span covers `line`."""
        for start, end, name in self.spans.get(location, ()):
            if start <= line <= end:
                return name
        return None


def _contributor(base: BaseShape, sources: Sources | None, applied: frozenset[str]) -> str | None:
    """The trait or resource type that supplied this, when it was not written here.

    Reported **only** when the containing declaration is one this site actually
    applied. `Sources` already bounds the span exactly, so this is the second of
    two independent checks rather than the only one — and it is what stops a
    coincidence inside an unrelated declaration from being reported as a fact.
    """
    if sources is None or not applied or not base.key_pos.is_known:
        return None
    found = sources.containing(base.location, base.key_pos.line)
    return found if found in applied else None


def _note(base: BaseShape, sources: Sources | None, applied: frozenset[str], root: str) -> str:
    parts = (_contributor(base, sources, applied), _at(base.location, base.key_pos, root))
    return ', '.join(part for part in parts if part)


def _at(location: str, position: Position, root: str) -> str:
    if not position.is_known:
        return ''
    return f'{location.removeprefix(root) or location}:{position.line}'


def _aligned(lines: list[_Line]) -> Iterator[str]:
    width = max((len(line.text) for line in lines if line.note), default=0)
    for line in lines:
        yield f'{line.text:<{width}}  # {line.note}' if line.note else line.text


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
    yield from _aligned([_Line(f'{base.name or "<anonymous>"}:', _where(base, root)), *_body(base, level)])


def _body(base: BaseShape, level: _Level) -> Iterator[_Line]:
    named = _type_name(base)
    yield _Line(f'{level.indent}type: {named}')
    parents = [parent.name or '<anonymous>' for parent in base.inherits]
    # `inherits: [User]` under `type: User` is the same fact twice — `_type_name`
    # returns the sole parent's name by construction. Two parents or more is
    # where the line earns its place, because `type:` cannot show both.
    if parents and parents != [named]:
        yield _Line(f'{level.indent}inherits: [{", ".join(parents)}]')
    yield from _facets(base, level.indent)

    # The structure is read from the *projected* shape, so a type defined by a
    # JSON schema opens like any other. Its own facets above are the RAML ones,
    # which for a schema type is nothing: the schema carries the constraints.
    view = projected(base)
    shape = view.shape
    if isinstance(shape, ObjectShape):
        yield from _properties(view, shape, level)
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

    Emitted by PyYAML for the reason `_dumped` gives. The allowlist this used
    instead was safe against punctuation — pattern properties are why it exists,
    since `//` strips to an empty key, which broke three corpus fixtures — and
    silently wrong against a name that merely *reads* as another type: a
    property called `yes`, `no`, `on` or `null` matched the allowlist, emitted
    bare, and loaded back as a bool or a null. A key is a value too.
    """
    return _dumped(name)


def _member(base: BaseShape, key: str, level: _Level) -> Iterator[_Line]:
    if level.opens(base) and not isinstance(base.shape, RecursiveShape):
        yield _Line(f'{level.indent}{key}:')
        yield from _body(base, level.inside(base))
    else:
        yield _Line(f'{level.indent}{key}: {_type_name(base)}')


# -- reading the model --------------------------------------------------------


def _has_structure(base: BaseShape) -> bool:
    """Whether this type contains anything an extra level would reveal."""
    shape = projected(base).shape
    if isinstance(shape, ObjectShape):
        return bool(shape.properties or shape.pattern_properties)
    if isinstance(shape, ArrayShape):
        return shape.items is not None
    if isinstance(shape, UnionShape):
        return bool(shape.any_of)
    return False


def _type_name(base: BaseShape, *, nested: bool = False) -> str:
    """What to call this type in one word.

    `alias` first, and that is not a detail: `address: Address` and
    `UserList: User[]` both put an *alias* of the referenced type in place
    rather than the declaration (docs/07 § 3.6). Reading `type` instead prints
    `object` for both — true, and useless.

    A recursion marker names the type it closes back to. Its own `type` is
    `recursive`, which tells the reader nothing about which cycle they are in.

    An **anonymous union names its members** — `integer | nil`, not `union`.
    That is naming and not expansion, so it is not gated on `--depth`: a union
    reaching the depth limit as the bare word `union` tells the reader nothing
    at all, and one more level was showing the member names they wanted. JSON
    Schema makes this the common case rather than a corner — every nullable
    field is a `oneOf` of the type and `null`.

    `nested` stops the join one level down, so a union of unions reads
    `union | string` instead of unrolling an arbitrary tree onto one line.
    """
    if isinstance(base.shape, RecursiveShape):
        return base.shape.head.name or 'recursive'
    if base.alias is not None and base.alias.name:
        return base.alias.name
    if len(base.inherits) == 1 and base.inherits[0].name:
        return base.inherits[0].name
    shape = projected(base).shape
    if not nested and isinstance(shape, UnionShape) and shape.any_of:
        return ' | '.join(_type_name(member, nested=True) for member in shape.any_of)
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
    return _at(base.location, base.key_pos, root)


def _facets(base: BaseShape, indent: str) -> Iterator[_Line]:
    """Every constraint the kind holds, in RAML spelling.

    Read off `__slots__` rather than from a table, for the reason the golden
    projector does (docs/14 § 2): a facet added to a kind has to show up here
    without this module being edited, or the view silently omits a constraint.

    Through the projection, so a scalar JSON schema shows its bounds. A
    `JsonShape` holds no facet slots of its own — the constraints are inside the
    compiled schema — so without this a `uuid` defined as
    `{"type": "string", "minLength": 36}` rendered as bare `string`.
    """
    shape = projected(base).shape
    if shape is not None:
        for slot in _slots(type(shape)):
            if slot in _NOT_A_FACET:
                continue
            value = getattr(shape, slot, None)
            if isinstance(value, ScalarFacet):
                yield _Line(f'{indent}{_SPELLINGS.get(slot, _camel(slot))}: {_scalar(value.value)}')
    if base.enum is not None:
        # Dumped as a list, so the flow context quotes a member containing a
        # comma rather than silently splitting it into two.
        yield _Line(f'{indent}enum: {_dumped([str(member.raw) for member in base.enum])}')
    if base.description is not None and base.description.value:
        first = base.description.value.strip().splitlines()[0]
        yield _Line(f'{indent}description: {_dumped(first)}')


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
    if isinstance(value, re.Pattern):
        # The facet holds a *compiled* pattern, and `str()` of one is
        # `re.compile('…')` — Python's repr where the author's regex belongs.
        # Invisible until schema types began rendering their facets, because
        # `pattern:` reaches a reader through `uuid` far more often than
        # through a RAML declaration.
        value = value.pattern
    # Not `str(value)` first: that turns `maxLength: 36` into the *string* "36",
    # which PyYAML then quotes to preserve — correctly, and uselessly.
    return _dumped(value if isinstance(value, str | int | float) else str(value))


def _dumped(value: Any) -> str:
    """One value as YAML, emitted by PyYAML rather than by a rule written here.

    Deciding when a scalar needs quoting is not a short rule and this module has
    no business owning one. A first attempt did — a denylist of `': '`, `' #'`
    and a few leading characters — and it was wrong for every string that merely
    *looks* like something else: `yes`, `null` and `1.0` all round-tripped as a
    bool, a null and a float. PyYAML is already a hard dependency and gets all of
    those, plus tabs and the flow-context comma, right by construction.

    Only the values go through it. The document's *shape* is still written by
    hand, because the whole point of this view is the aligned `# origin` column
    and an emitter cannot produce comments (§ 9.2).

    A scalar is memoised, because `safe_dump` builds an emitter, a serialiser
    and a resolver per call — 13.7 µs against the 0.15 µs of the rule it
    replaced — and the inputs repeat almost perfectly: property names, `string`,
    `true`, the same bounds and media types over and over. Rendering the whole
    benchmark corpus measured 664 ms down to 254 ms off a thirteen-entry cache.
    Lists — `enum` is the only one — are not hashable and fall through.
    """
    if type(value) in (str, int, float):
        return _dumped_scalar(value)
    return _emit(value)


@lru_cache(maxsize=4096)
def _dumped_scalar(value: str | float) -> str:
    """`type(...) in`, not `isinstance`, above: `bool` is a subclass of `int`
    and `hash(True) == hash(1)`, so the two would share one cache entry and the
    first of them to arrive would decide whether the other reads `true` or `1`.
    """
    return _emit(value)


def _emit(value: Any) -> str:
    text = yaml.safe_dump(value, default_flow_style=True, width=_UNWRAPPED, allow_unicode=True)
    return text.rstrip('\n').removesuffix('\n...').rstrip()


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


# -- endpoints ----------------------------------------------------------------


def render_endpoint(
    endpoint: EndPoint, *, depth: int = 1, root: str = '', sources: Sources | None = None
) -> Iterator[str]:
    """One resource as RAML, with everything that reached it already applied.

    The endpoint is the entity that needs this most. It accumulates a resource
    type, any number of traits, security inherited from the API root, and URI
    parameters propagated down from every ancestor — none of which is visible at
    the place it is written. `sources` is what lets a merged-in item name the
    trait it came from; without it the item still carries a file and a line.
    """
    level = _Level(depth=depth, root=root, indent='  ', seen=frozenset())
    lines = [_Line(f'{endpoint.full_uri}:', _at(endpoint.location, endpoint.key_pos, root))]
    lines += list(_endpoint_body(endpoint, level, sources))
    yield from _aligned(lines)


def render_operation(
    operation: Operation, path: str, *, depth: int = 1, root: str = '', sources: Sources | None = None
) -> Iterator[str]:
    """One method, under the resource it belongs to."""
    level = _Level(depth=depth, root=root, indent='  ', seen=frozenset())
    lines = [_Line(f'{path}:')]
    lines += list(_operation(operation, level, sources))
    yield from _aligned(lines)


def _prose(owner: Any, indent: str) -> Iterator[_Line]:
    """`displayName` and `description`, wherever the model carries them.

    Omitted until now, and they are half of why a reader opens an endpoint at
    all: a resource's `description` says what it is for, and a response's says
    what the status code *means*, which no other line here conveys. A trait
    supplies most of them on a real document, so they are exactly the kind of
    thing this view exists to gather up.

    First line only, as everywhere else here: a description may be a paragraph,
    and the view is meant to fit a screen.
    """
    for facet in ('display_name', 'description'):
        value = getattr(owner, facet, None)
        if value is not None and value.value and value.value.strip():
            first = value.value.strip().splitlines()[0]
            yield _Line(f'{indent}{_SPELLINGS.get(facet, _camel(facet))}: {_dumped(first)}')


def _endpoint_body(endpoint: EndPoint, level: _Level, sources: Sources | None) -> Iterator[_Line]:
    applied = _applied(endpoint)
    yield from _prose(endpoint, level.indent)
    if endpoint.resource_type is not None:
        yield _Line(f'{level.indent}type: {endpoint.resource_type.name}')
    if endpoint.traits:
        yield _Line(f'{level.indent}is: [{", ".join(ref.name for ref in endpoint.traits)}]')
    yield from _secured(endpoint.secured_by, level)
    # Ancestor-declared parameters come first and are the ones a reader is least
    # likely to have in mind: they are written on a resource further up the path.
    yield from _parameters(endpoint.uri_parameters, 'uriParameters', level, sources, applied)
    for operation in endpoint.operations.values():
        yield from _operation(operation, level, sources, applied)


def _operation(
    operation: Operation, level: _Level, sources: Sources | None, inherited: frozenset[str] = frozenset()
) -> Iterator[_Line]:
    applied = inherited | {ref.name for ref in operation.traits}
    inner = replace(level, indent=level.indent + '  ')
    yield _Line(f'{level.indent}{operation.method}:', _at(operation.location, operation.key_pos, level.root))
    yield from _prose(operation, inner.indent)
    if operation.protocols:
        # Narrows the API's own list for this method, and nothing else in the
        # view says so — a reader looking for "is this one HTTPS-only?" has no
        # other line to read.
        yield _Line(f'{inner.indent}protocols: [{", ".join(operation.protocols)}]')
    if operation.traits:
        yield _Line(f'{inner.indent}is: [{", ".join(ref.name for ref in operation.traits)}]')
    yield from _secured(operation.secured_by, inner)

    if operation.request is not None:
        yield from _message(operation.request, inner, sources, applied)
    yield from _responses(operation.responses, inner, sources, applied)


def _message(owner: Any, level: _Level, sources: Sources | None, applied: frozenset[str]) -> Iterator[_Line]:
    """What a caller sends: headers, query and body.

    Shared because a `Request` and a security scheme's `describedBy` carry the
    same four fields — `SecuritySchemeDescription`'s own docstring calls it "the
    same node vocabulary as an operation", so this follows the model rather than
    a coincidence.
    """
    yield from _parameters(owner.headers, 'headers', level, sources, applied)
    yield from _parameters(owner.query_parameters, 'queryParameters', level, sources, applied)
    if owner.query_string is not None:
        yield _Line(f'{level.indent}queryString: {_type_name(owner.query_string)}')
    yield from _bodies(getattr(owner, 'bodies', None) or {}, level, sources, applied)


def _responses(
    responses: dict[str, Response], level: _Level, sources: Sources | None, applied: frozenset[str]
) -> Iterator[_Line]:
    """Every response, for an operation and for a scheme's `describedBy` alike.

    One function because the two were written twice and had already started to
    drift: `_prose` had to be added to both copies by hand, and a third addition
    reaching only one of them would be invisible — the output stays loadable
    RAML, just missing a line, so corpus law 13 would not catch it either.
    """
    if not responses:
        return
    yield _Line(f'{level.indent}responses:')
    for response in responses.values():
        code = replace(level, indent=level.indent + '  ')
        yield _Line(f'{code.indent}{response.code}:', _at(response.location, response.key_pos, level.root))
        body = replace(code, indent=code.indent + '  ')
        # What the code *means* — the one thing a bare `404:` cannot say, and on
        # a trait-heavy document the description is the only part of a response
        # that differs between two operations sharing a body.
        yield from _prose(response, body.indent)
        yield from _parameters(response.headers, 'headers', body, sources, applied)
        yield from _bodies(response.bodies, body, sources, applied)


def _bodies(
    bodies: dict[str, Body], level: _Level, sources: Sources | None, applied: frozenset[str]
) -> Iterator[_Line]:
    if not bodies:
        return
    inner = replace(level, indent=level.indent + '  ')
    yield _Line(f'{level.indent}body:')
    for media, body in bodies.items():
        if body.shape is None:
            yield _Line(f'{inner.indent}{_key(media)}: any')
            continue
        note = _note(body.shape, sources, applied, level.root)
        if level.opens(body.shape):
            yield _Line(f'{inner.indent}{_key(media)}:', note)
            yield from _body(body.shape, inner.inside(body.shape))
        else:
            yield _Line(f'{inner.indent}{_key(media)}: {_type_name(body.shape)}', note)


def _parameters(
    parameters: dict[str, Property], label: str, level: _Level, sources: Sources | None, applied: frozenset[str]
) -> Iterator[_Line]:
    if not parameters:
        return
    yield _Line(f'{level.indent}{label}:')
    for name, prop in parameters.items():
        key = name if prop.required else f'{name}?'
        yield from _one(key, prop.base, _contributor(prop.base, sources, applied), level)


def _label(scheme: SecurityScheme) -> str:
    """One scheme as it is written, with any narrowed OAuth scopes."""
    if scheme.is_null:
        # `securedBy: [null]` *removes* inherited security (docs/09 § A3), and
        # `null` is how RAML spells that — so it round-trips as itself.
        return 'null'
    if scheme.compiled_params:
        return f'{scheme.name} ({", ".join(scheme.compiled_params)})'
    return scheme.name


def _secured(schemes: list[SecurityScheme], level: _Level) -> Iterator[_Line]:
    """`securedBy:`, and what each scheme adds to the request.

    A scheme's `describedBy` declares headers, query parameters and responses
    that a caller using it must supply or expect, and none of it was rendered —
    so an operation's `Authorization` header, the one thing every caller needs,
    appeared nowhere in the view.

    **One block per scheme, never merged into the operation.** Spec § Applying
    Security Schemes: a method "can be authenticated by *any* of the specified
    security schemes", so three schemes are three ways to call it, not one call
    carrying all three headers. Flattening them into the operation's `headers:`
    would state something false. It would also need a precedence rule for a
    response code the operation *and* the scheme both declare — a `401` from
    both a trait and the scheme is the ordinary case here — and the spec defines
    none, because it never merges them.

    The flat form is kept when no scheme contributes anything, which is most
    documents; a block per name with nothing in it is worse than a list.
    """
    if not schemes:
        return
    removed = any(scheme.is_null for scheme in schemes)
    note = 'null removes inherited security' if removed else ''
    described = [(scheme, _described(scheme)) for scheme in schemes]
    if not any(description is not None for _, description in described):
        yield _Line(f'{level.indent}securedBy: [{", ".join(_label(s) for s, _ in described)}]', note)
        return

    yield _Line(f'{level.indent}securedBy:', note)
    inner = replace(level, indent=level.indent + '  ')
    for scheme, description in described:
        if description is None:
            yield _Line(f'{inner.indent}{_key(_label(scheme))}:')
            continue
        where = _at(description.location, description.value_pos, level.root) if description.value_pos else ''
        yield _Line(f'{inner.indent}{_key(_label(scheme))}:', where)
        # No `sources`: attribution names the trait or resource type a merged-in
        # item came from, and nothing here was merged in — a scheme's headers
        # are the scheme's, which the block it sits in already says.
        body = replace(inner, indent=inner.indent + '  ')
        yield from _message(description, body, None, frozenset())
        yield from _responses(description.responses, body, None, frozenset())


def _described(scheme: SecurityScheme) -> SecuritySchemeDescription | None:
    """The scheme's `describedBy`, if it declares anything worth showing."""
    definition = scheme.definition.resolved() if scheme.definition is not None else None
    description = definition.described_by if definition is not None else None
    if description is None:
        return None
    has_content = description.headers or description.query_parameters or description.responses
    return description if has_content or description.query_string is not None else None


def _applied(endpoint: EndPoint) -> frozenset[str]:
    """The declarations in force on this resource, by name.

    What bounds attribution: a merged-in item may only be credited to something
    this resource or its methods actually applied.
    """
    names = {ref.name for ref in endpoint.traits}
    if endpoint.resource_type is not None:
        names.add(endpoint.resource_type.name)
    return frozenset(names)
