"""Reading the effective tree, for a generator.

The metamodel — the three constructs, the address index, the containment walk —
is `raml_codegen.walk`, which is vendored from `fastraml/views/bindings/` beside
the generated `tree.py` it reads its table out of. Nothing about *this* package
is in there, and nothing the contract states is in here.

What is left is the two questions a generator asks that the contract does not
answer: which declarations does it emit source for, and what does one shape hold
that a Python annotation cares about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .walk import Tree as _Tree
from .walk import UnreadableTree, is_recursion, is_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .tree import Endpoint, Property, SecurityScheme, Shape, ShapeDeclarationsByFile, ShapeNode

__all__ = [
    'Declaration',
    'Tree',
    'UnreadableTree',
    'is_recursion',
    'is_ref',
    'items_of',
    'members_of',
    'properties_of',
]


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


@dataclass(frozen=True, slots=True)
class Declaration:
    """One named declaration, and the file it was written in."""

    file: str
    name: str
    address: str
    shape: Shape


class Tree(_Tree):
    """The contract's reader, plus the sections a generator emits from."""

    __slots__ = ()

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
