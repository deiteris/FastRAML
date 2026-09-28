"""What a file, or an entity, wrote (docs/16 § 10).

One answer for every consumer that lists a document as its author wrote it:
the outline now, completion, rename and semantic tokens later. It reads facts
the model records, and selects by `location` over the merged model, so a file
an Extension adds to lists the type it declared in the master's table and the
method it added to a master resource.

Which template contributed a member is not recorded: `wrote` tells it from
the spans, and that is exact. A trait or resource type is declared in its
table or its own fragment, never inside a resource, a method or a type, so a
member it contributed lies outside its parent's span even in the parent's
file, and one inside was written there. The placement law makes spans hold
what they were written with (`research/language-service-architecture.md`
§ 7, § 12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from fastraml.parser.fragments import APIFragment, Library
from fastraml.types.complex_ import ArrayShape, ObjectShape

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from fastraml.parser.documentation import DocumentationItem
    from fastraml.parser.endpoints import Body, EndPoint, Operation
    from fastraml.parser.fragments import Declaration
    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape, Parameter, PatternProperty, Property

__all__ = [
    'Placed',
    'WrittenResource',
    'bodies',
    'declarations',
    'documentation',
    'items',
    'parameters',
    'pattern_properties',
    'properties',
    'resources',
    'wrote',
]


class Placed(Protocol):
    """An entity the model placed: its file, and where its key and value are."""

    @property
    def location(self) -> str: ...
    @property
    def key_pos(self) -> Position: ...
    @property
    def value_pos(self) -> Position: ...


def wrote(parent: Placed, location: str, key: Position) -> bool:
    """Whether `parent` wrote what is keyed at `key` in `location`: the same
    file, inside its span.

    Exact, as the module docstring says; the one span test, here rather than
    in each reader.
    """
    return location == parent.location and _within(_extent(parent), key)


def _extent(parent: Placed) -> tuple[int, int, int, int]:
    """`parent`'s span from its key through its value, as numbers: built for
    every member of every type, where a `Position` cost the outline a third.
    """
    key, value = parent.key_pos, parent.value_pos
    if not value.is_known:
        return key.line, key.column, key.end_line, key.end_column
    start = min((key.line, key.column), (value.line, value.column))
    end = max((key.end_line, key.end_column), (value.end_line, value.end_column))
    return (*start, *end)


def _within(extent: tuple[int, int, int, int], key: Position) -> bool:
    line, column, end_line, end_column = extent
    return (
        key.is_known
        and (line, column) <= (key.line, key.column)
        and (key.end_line, key.end_column)
        <= (
            end_line,
            end_column,
        )
    )


def parameters(owner: Placed, written: Mapping[str, Parameter]) -> Iterator[tuple[str, Parameter]]:
    """The parameters `owner` wrote. A parameter is a record placed at its
    key, in the file its shape was written in.
    """
    return ((name, param) for name, param in written.items() if wrote(owner, param.base.location, param.key_pos))


def declarations(raml: Raml, uri: str) -> Iterator[tuple[str, str, Declaration]]:
    """Every declaration written in `uri`, as `(key, name, entity)` in F1's
    order: its own fragment's, and those it added to the tables of the API
    an Overlay or Extension merges into, the one fragment a merge writes to
    (docs/19 § 4).
    """
    fragments = (raml.fragments.get(uri), raml.entry_point)
    for fragment in dict.fromkeys(each for each in fragments if isinstance(each, (APIFragment, Library))):
        for key, name, entity in fragment.declarations():
            if entity.location == uri:
                yield key, name, entity


def documentation(raml: Raml, uri: str) -> Iterator[DocumentationItem]:
    """The API's documentation items written in `uri`."""
    api = raml.entry_point
    if isinstance(api, APIFragment):
        yield from (item for item in api.documentation if item.location == uri)


def properties(base: BaseShape) -> Iterator[tuple[str, Property]]:
    """The properties `base` declares, not those it inherits: an inherited one
    keeps its declaration's shape, which a parent holds too. An alias shares
    its referent's containers (docs/07 § 3), so it declares none.

    Written inside `base` too, by `wrote`'s test: recursion marking gives an
    inherited property that closes a cycle a shape of its own, and a
    property a template merged into a declaration lies in the template.
    """
    shape = base.shape
    if base.alias is not None or not isinstance(shape, ObjectShape) or not shape.properties:
        return
    inherited = {prop.base.id for parent in base.inherits for prop in _object_properties(parent)}
    extent = _extent(base)
    for key, prop in shape.properties.items():
        member = prop.base
        if member.id not in inherited and member.location == base.location and _within(extent, member.key_pos):
            yield key, prop


def pattern_properties(base: BaseShape) -> Iterator[tuple[str, PatternProperty]]:
    """`properties`, for the `/regex/` keys."""
    shape = base.shape
    if base.alias is not None or not isinstance(shape, ObjectShape) or not shape.pattern_properties:
        return
    inherited = {
        pattern.base.id
        for parent in base.inherits
        if isinstance(parent.shape, ObjectShape)
        for pattern in (parent.shape.pattern_properties or {}).values()
    }
    extent = _extent(base)
    for key, pattern in shape.pattern_properties.items():
        member = pattern.base
        if member.id not in inherited and member.location == base.location and _within(extent, member.key_pos):
            yield key, pattern


def items(base: BaseShape) -> BaseShape | None:
    """The items `base` wrote, as an `items:` facet (F4, docs/06 § 3), and
    inside it, as `properties` asks.
    """
    shape = base.shape
    if base.alias is not None or not isinstance(shape, ArrayShape) or not shape.items_written:
        return None
    found = shape.items
    return found if found is not None and wrote(base, found.location, found.key_pos) else None


def bodies(written: Mapping[str, Body]) -> list[list[Body]]:
    """Each body as written: one per media type key, and the bodies one
    `body:` without a media type became, together (F3, docs/08 § 6.3).
    """
    found = [[body] for body in written.values() if body.media_type_written]
    defaults = [body for body in written.values() if not body.media_type_written]
    return [*found, defaults] if defaults else found


@dataclass(slots=True, eq=False)
class WrittenResource:
    """A resource as one file wrote it.

    `here` says whether its key is written in that file. A resource another
    file declared is listed only for what this file added to it, as an
    Extension adds a method to a master resource.
    """

    endpoint: EndPoint
    here: bool
    operations: list[Operation] = field(default_factory=list)
    resources: list[WrittenResource] = field(default_factory=list)


def resources(raml: Raml, uri: str) -> list[WrittenResource]:
    """The resources `uri` wrote, or added to, as the file nests them."""
    tops = (endpoint for endpoint in raml.endpoints.values() if endpoint.full_uri == endpoint.uri)
    return [found for endpoint in tops if (found := _resource(endpoint, uri)) is not None]


def _resource(endpoint: EndPoint, uri: str) -> WrittenResource | None:
    here = endpoint.location == uri
    written = WrittenResource(endpoint, here)
    for operation in endpoint.operations.values():
        # A resource another file declared has no span in this one, so what
        # this file added to it is known by location alone.
        if wrote(endpoint, operation.location, operation.key_pos) if here else operation.location == uri:
            written.operations.append(operation)
    for child in endpoint.endpoints.values():
        found = _resource(child, uri)
        if found is not None:
            written.resources.append(found)
    return written if here or written.operations or written.resources else None


def _object_properties(base: BaseShape) -> Iterator[Property]:
    shape = base.shape
    if isinstance(shape, ObjectShape):
        yield from (shape.properties or {}).values()
