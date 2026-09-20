"""Read the effective tree.

A consumer of the tree does four things and no others (docs/16 § 11.7): descend
containment, follow a link when it chooses to, stop at a recursion marker, and
keep no ancestor set. This module is all four of them, and it is the only module
in the package that knows what the JSON means. The rest decide Python spellings.

The types come from `raml_codegen.tree`, which is generated from the emitter, so
a key that stops arriving is a type error here rather than a `KeyError` in the
middle of a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .tree import (
        DescribedBy,
        Document,
        Endpoint,
        Parameter,
        Property,
        Recursion,
        Ref,
        SecurityScheme,
        Shape,
        ShapeDeclarationsByFile,
        ShapeNode,
    )

__all__ = ['Declaration', 'Tree', 'is_recursion', 'is_ref', 'items_of', 'members_of', 'properties_of']

#: The one tree format this package reads. The envelope exists so a consumer can
#: refuse a representation it does not know, instead of guessing from its fields
#: (docs/16 § 11.9). Refusing is what makes emitting it worthwhile.
FORMAT = 'fastraml-tree'
FORMAT_VERSION = 1
VIEW = 'effective'


def properties_of(shape: Shape) -> dict[str, Property]:
    """Return the properties a shape declares, or none.

    Only an `object` has `properties`, so narrow on `type` before reading it.
    The generated binding is what makes that a type error rather than a
    run-time surprise (docs/16 § 11.11a).
    """
    return shape.get('properties', {}) if shape['type'] == 'object' else {}


def items_of(shape: Shape) -> ShapeNode | None:
    """What an array holds, or nothing."""
    return shape.get('items') if shape['type'] == 'array' else None


def members_of(shape: Shape) -> list[ShapeNode]:
    """A union's members, or none."""
    return shape.get('any_of', []) if shape['type'] == 'union' else []


class UnreadableTree(ValueError):
    """The document is not a tree this version understands."""


@dataclass(frozen=True, slots=True)
class Declaration:
    """One named declaration, and the file it was written in."""

    file: str
    name: str
    address: str
    shape: Shape


def is_ref(node: ShapeNode | None) -> TypeGuard[Ref]:
    """A link: its sole key is the test, since an expanded shape carries `id`."""
    return isinstance(node, dict) and set(node) == {'$ref'}


def is_recursion(node: ShapeNode | None) -> TypeGuard[Recursion]:
    """The structure repeats from here; do not expand.

    `type` carries the marker rather than a key of its own, so a consumer that
    switches on `type` and has not handled it fails loudly (docs/16 § 11.7).
    """
    return isinstance(node, dict) and node.get('type') == 'recursive'


class Tree:
    """One `fastraml tree` document, with its addresses indexed."""

    __slots__ = ('_index', 'document')

    def __init__(self, document: Document) -> None:
        self.document = document
        self._index: dict[str, Shape] = {}
        for shape in self._reachable():
            address = shape.get('id')
            if address is not None:
                self._index.setdefault(address, shape)

    @classmethod
    def of(cls, document: object) -> Tree:
        """Check the envelope, then index the document.

        A tree from a later format version may have renamed a field this
        package reads. Reading it anyway produces a client that compiles and is
        wrong, so refuse anything the three envelope fields do not match.
        """
        if not isinstance(document, dict):
            raise UnreadableTree(f'not a tree document: {type(document).__name__}')
        found = (document.get('format'), document.get('format_version'), document.get('view'))
        if found != (FORMAT, FORMAT_VERSION, VIEW):
            raise UnreadableTree(
                f'expected {FORMAT} v{FORMAT_VERSION} ({VIEW}), got '
                f'{found[0]!r} v{found[1]!r} ({found[2]!r}) -- regenerate with a matching fastraml'
            )
        return cls(cast('Document', document))

    # -- the three constructs --------------------------------------------------

    def resolve(self, node: ShapeNode | None) -> Shape | Recursion | None:
        """Follow a link, once.

        A recursion marker comes back as itself. Treating it as a link would
        break the traversal law: a walker that expands links cannot tell a
        repeat from a fresh subtree (docs/16 § 11.7).
        """
        if node is None or is_recursion(node):
            return node
        if is_ref(node):
            return self._index.get(node['$ref'])
        # `TypeGuard` narrows the branch it is true in and says nothing about
        # the other, so the two checks above leave `Shape` to be asserted.
        return cast('Shape', node)

    def at(self, address: str) -> Shape | None:
        """The shape an address names, or nothing."""
        return self._index.get(address)

    def content_of(self, shape: Shape) -> Shape:
        """Return what a shape is, rather than how it arrived.

        A `json` shape carries its schema twice: `json_schema` in JSON Schema's
        own vocabulary, and `projection` as the nearest RAML shape. Read the
        projection as the type (docs/16 § 11.10). The `json` shape itself has no
        properties and no facets, because the spec forbids a JSON-schema type
        from taking part in inheritance and the parser decodes nothing from it.
        """
        # `shape['type']`, not `.get`: the subscript narrows the union to the
        # one variant that has a `projection`, and `.get` does not.
        if shape['type'] == 'json':
            projection = shape.get('projection')
            if projection is not None:
                return projection
        return shape

    # -- what the document holds -----------------------------------------------

    def types(self) -> Iterator[Declaration]:
        """Every `types:` declaration, in declaration order, by file."""
        return self._declared(self.document['types'])

    def endpoints(self) -> Iterator[tuple[str, Endpoint]]:
        return iter(self.document['endpoints'].items())

    def security_schemes(self) -> Iterator[tuple[str, SecurityScheme]]:
        for schemes in self.document['security_schemes'].values():
            yield from schemes.items()

    def _declared(self, by_file: ShapeDeclarationsByFile) -> Iterator[Declaration]:
        for file, declarations in by_file.items():
            for name, node in declarations.items():
                # An alias never reaches the output as a node (docs/16 § 11.8),
                # so a declaration that is a bare link names another declaration
                # and resolves. One that does not is a tree we cannot read.
                if is_ref(node):
                    address: str | None = node['$ref']
                else:
                    address = cast('Shape', node).get('id')
                found = self.resolve(node)
                if found is None or is_recursion(found) or address is None:
                    continue
                # `is_recursion` is a `TypeGuard`, which narrows only the branch
                # it is true in; the `Shape` on this side has to be asserted.
                yield Declaration(file=file, name=name, address=address, shape=cast('Shape', found))

    # -- indexing ---------------------------------------------------------------

    def _reachable(self) -> Iterator[Shape]:
        """Yield every addressed shape, by containment.

        No ancestor set and no visited set. Containment is a tree, and the two
        constructs that would make it a graph — a link and a recursion marker —
        are both stopped at.
        """
        document = self.document
        for by_file in (document['types'], document['annotation_types']):
            for declarations in by_file.values():
                for node in declarations.values():
                    yield from _shapes_in(node)
        for endpoint in document['endpoints'].values():
            yield from _endpoint_shapes(endpoint)
        for schemes in document['security_schemes'].values():
            for scheme in schemes.values():
                described = scheme.get('described_by')
                if described is not None:
                    yield from _described_shapes(described)


def _shapes_in(node: ShapeNode | None) -> Iterator[Shape]:
    """A shape and everything it contains. Links and markers are not followed."""
    if node is None or is_ref(node) or is_recursion(node):
        return
    shape = cast('Shape', node)
    yield shape

    for member in shape.get('inherits', ()):
        yield from _shapes_in(member)
    for facet in shape.get('declared_facets', {}).values():
        yield from _shapes_in(facet['type'])
    if shape['type'] == 'object':
        for prop in shape.get('properties', {}).values():
            yield from _shapes_in(prop['type'])
        for pattern in shape.get('pattern_properties', {}).values():
            yield from _shapes_in(pattern['type'])
    elif shape['type'] == 'array':
        yield from _shapes_in(shape.get('items'))
    elif shape['type'] == 'union':
        for member in shape.get('any_of', []):
            yield from _shapes_in(member)
    elif shape['type'] == 'json':
        # The projection is the type (`content_of`), so its nested shapes are
        # addressable like any other -- a schema type is not a leaf (docs/16 § 2.6).
        yield from _shapes_in(shape.get('projection'))


def _endpoint_shapes(endpoint: Endpoint) -> Iterator[Shape]:
    yield from _parameter_shapes(endpoint.get('uri_parameters', {}))
    for operation in endpoint['operations'].values():
        yield from _parameter_shapes(operation.get('query_parameters', {}))
        yield from _parameter_shapes(operation.get('headers', {}))
        yield from _shapes_in(operation.get('query_string'))
        for body in operation.get('bodies', {}).values():
            yield from _shapes_in(body)
        for response in operation['responses'].values():
            yield from _parameter_shapes(response.get('headers', {}))
            for body in response.get('bodies', {}).values():
                yield from _shapes_in(body)


def _described_shapes(described: DescribedBy) -> Iterator[Shape]:
    yield from _parameter_shapes(described.get('headers', {}))
    yield from _parameter_shapes(described.get('query_parameters', {}))
    yield from _shapes_in(described.get('query_string'))
    for response in described.get('responses', {}).values():
        for body in response.get('bodies', {}).values():
            yield from _shapes_in(body)


def _parameter_shapes(parameters: dict[str, Parameter]) -> Iterator[Shape]:
    for parameter in parameters.values():
        yield from _shapes_in(parameter['type'])
