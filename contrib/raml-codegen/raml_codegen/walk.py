"""Reading a `fastraml tree` document.

Copied verbatim by `python -m fastraml.views.bindings python --runtime FILE`. Do
not edit the copy. Edit `fastraml/views/bindings/static/walk.py`, which is this
file; it holds no generated character, and reads its one table out of `tree.py`
beside it. `tests/unit/test_bindings.py` fails when the two disagree.

The metamodel is three constructs (docs/16-graph.md § 6.1):

    {"$ref": <address>}                            a link -- look the target up
    {"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
    anything else                                  containment -- descend

A consumer descends containment, follows a link when it chooses to, and stops at
a recursion marker. It maintains no ancestor set.

This holds only what the contract states. Naming, page routing and path
nesting belong to the consumer.

The table is `tree.CHILDREN`, generated from the emitter, so a facet that starts
holding a shape is descended without this file being edited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, TypeGuard, cast

from .tree import CHILDREN, FORMAT, FORMAT_VERSION, KINDS, VIEW

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .tree import Document, Recursion, Ref, Shape, ShapeNode

__all__ = ['Tree', 'UnreadableTree', 'is_recursion', 'is_ref']

#: The one `type` value that is not a shape kind.
RECURSION_TYPE = 'recursive'


class UnreadableTree(ValueError):
    """The document is not a tree this version of the contract understands."""


def is_ref(node: object) -> TypeGuard[Ref]:
    """A link: its sole key is the test, since an expanded shape carries `id`."""
    return isinstance(node, dict) and set(node) == {'$ref'}


def is_recursion(node: object) -> TypeGuard[Recursion]:
    """The structure repeats from here; do not expand.

    Spelled in `type` rather than a key of its own, so a consumer that switches
    on `type` and has not handled it fails loudly. Distinct from a link on
    purpose: merging the two would force every consumer to carry an ancestor set.
    """
    return isinstance(node, dict) and node.get('type') == RECURSION_TYPE


def is_shape(node: object) -> TypeGuard[Shape]:
    """An expanded type: neither a link nor a marker, and it names its kind."""
    return isinstance(node, dict) and node.get('type') in KINDS


class Tree:
    """One `fastraml tree` document, with its addresses indexed."""

    __slots__ = ('_index', 'document')

    def __init__(self, document: Document) -> None:
        self.document = document
        self._index: dict[str, Shape] = {}
        for shape in self.shapes():
            address = shape.get('id')
            if address is not None:
                self._index.setdefault(address, shape)

    @classmethod
    def of(cls, document: object) -> Self:
        """Check the envelope, then index the document.

        A tree from a later format version may have renamed a field a consumer
        reads. Reading it anyway produces output that is wrong rather than
        absent, so refuse anything the three envelope fields do not match
        (docs/16 § 6).
        """
        return cls(cls.check(document))

    @staticmethod
    def check(document: object) -> Document:
        """The envelope alone, for a caller that wants to refuse a document.

        `of` indexes as well, which is the whole walk; a loader that only needs
        to know whether it can read the file should not pay for that and then
        discard it.
        """
        if not isinstance(document, dict):
            raise UnreadableTree(f'not a tree document: {type(document).__name__}')
        found = (document.get('format'), document.get('format_version'), document.get('view'))
        if found != (FORMAT, FORMAT_VERSION, VIEW):
            raise UnreadableTree(
                f'expected {FORMAT} v{FORMAT_VERSION} ({VIEW}), got '
                f'{found[0]!r} v{found[1]!r} ({found[2]!r}) -- regenerate with a matching fastraml'
            )
        return cast('Document', document)

    # -- the three constructs --------------------------------------------------

    def resolve(self, node: ShapeNode | None) -> Shape | Recursion | None:
        """Follow a link, once.

        A recursion marker comes back as itself. Treating it as a link would
        break the traversal law: a walker that expands links cannot tell a
        repeat from a fresh subtree.
        """
        if node is None or is_recursion(node):
            return node
        if is_ref(node):
            return self._index.get(node['$ref'])
        return node if is_shape(node) else None

    def at(self, address: str) -> Shape | None:
        """The shape an address names, or nothing."""
        return self._index.get(address)

    def content(self, shape: Shape) -> Shape:
        """What a shape is, rather than how it arrived.

        A `json` shape carries its schema twice: `json_schema` in JSON Schema's
        own vocabulary, and `projection` as the nearest RAML shape. The
        projection is the type (docs/16 § 6.2); the `json` shape itself carries
        no RAML properties or facets.
        """
        if shape['type'] != 'json':
            return shape
        projection = shape.get('projection')
        return shape if projection is None else cast('Shape', projection)

    # -- containment -------------------------------------------------------------

    def children(self, node: ShapeNode | None) -> Iterator[ShapeNode]:
        """The shape nodes directly under *node*.

        A link and a recursion marker have none: both are stops. Neither is
        followed here, so the walk needs no visited set.
        """
        if not is_shape(node):
            return
        yield from _under(node, 'ShapeBase')
        yield from _under(node, KINDS[node['type']])

    def shapes(self) -> Iterator[Shape]:
        """Every expanded shape in the document, by containment.

        No ancestor set and no visited set. Containment is a tree, and the two
        constructs that would make it a graph are both stopped at.
        """
        for node in _under(self.document, 'Document'):
            yield from self._expand(node)

    def _expand(self, node: ShapeNode | None) -> Iterator[Shape]:
        if not is_shape(node):
            return
        yield node
        for child in self.children(node):
            yield from self._expand(child)


def _under(value: Any, record: str) -> Iterator[Any]:
    """Every shape node one record holds, however deeply its own records nest.

    Generic over `CHILDREN`, so `Endpoint` -> `Operation` -> `Response` ->
    `bodies` is the table's business and not this function's.
    """
    if not isinstance(value, dict):
        return
    for key, container, holds, of in CHILDREN.get(record, ()):
        for item in _each(value.get(key), container):
            if holds == 'record':
                yield from _under(item, of)
            elif item is not None:
                yield item


def _each(value: Any, container: str) -> Iterator[Any]:
    if value is None:
        return
    if container == 'one':
        yield value
    elif container == 'list':
        yield from value
    elif container == 'map':
        yield from value.values()
    elif container == 'map_of_map':
        for inner in value.values():
            yield from inner.values()
