"""Custom-facet ancestor traversal and P7 bindings (docs/07 § 2).

Binding is independent of checking a value. P10 consumes the same ancestor
order for its diagnostics; P9 refreshes bindings when it replaces declarations.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from fastraml.datanode import DataNode
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape, Property


def referent(base: BaseShape) -> BaseShape:
    """The declaration an alias names, shared by facet binding and checking."""
    if base.alias is None:
        return base
    seen: set[BaseShape] = set()
    while base.alias is not None and base not in seen:
        seen.add(base)
        base = base.alias
    return base


def _parents(base: BaseShape) -> Sequence[BaseShape]:
    if base.link is not None and base.link.shape is not None:
        return (*base.inherits, base.link.shape)
    return base.inherits


def facet_ancestors(base: BaseShape) -> Iterator[BaseShape]:
    """Parents in breadth-first declaration order, through aliases and links.

    The declaring type is excluded: its facets constrain subtypes (docs/10 § 4).
    An alias is another name for its referent, not a new subtype.
    """
    seen: set[int] = set()
    queue = deque(_parents(referent(base)))
    while queue:
        current = referent(queue.popleft())
        if current.id in seen:
            continue
        seen.add(current.id)
        yield current
        queue.extend(_parents(current))


def bind_custom_facets(raml: Raml) -> None:
    """Publish the lazy index over this pass's model; report no value errors."""
    raml.custom_facet_refs = FacetBindings(raml)


type _Candidates = Property | tuple[Property, ...]


class FacetBindings(Mapping['DataNode', 'Sequence[Property]']):
    """Parser-owned bindings, materialized once when a consumer reads them.

    P9 replaces P7's index before either needs materializing in an ordinary
    unwrapped parse. P10 neither builds nor publishes it. A stopped parse still
    exposes the bindings of the last pass that published an index.
    """

    __slots__ = ('_bindings', '_raml')

    def __init__(self, raml: Raml) -> None:
        self._raml: Raml | None = raml
        self._bindings: dict[DataNode, _Candidates] | None = None

    def _index(self) -> dict[DataNode, _Candidates]:
        if self._bindings is None:
            assert self._raml is not None  # noqa: S101 - only materialization clears the registry
            self._bindings = _bindings(self._raml)
            self._raml = None
        return self._bindings

    def __getitem__(self, value: DataNode) -> Sequence[Property]:
        candidate = self._index()[value]
        return candidate if isinstance(candidate, tuple) else (candidate,)

    def __iter__(self) -> Iterator[DataNode]:
        return iter(self._index())

    def __len__(self) -> int:
        return len(self._index())


def _bindings(raml: Raml) -> dict[DataNode, _Candidates]:
    """Index already-resolved parents and declarations, without resolving names.

    DataNode identity survives inheritance copies. Distinct declarations remain
    candidates on invalid/ambiguous input; P10 owns duplicate diagnostics.
    """
    bindings: dict[DataNode, Property | list[Property] | tuple[Property, ...]] = {}
    seen: set[tuple[DataNode, int]] = set()
    for base in raml.shapes:
        if not base.custom_facets or base.alias is not None:
            continue
        for ancestor in facet_ancestors(base):
            for name, prop in ancestor.custom_facet_defs.items():
                value = base.custom_facets.get(name)
                if value is None:
                    continue
                key = (value, prop.base.id)
                if key not in seen:
                    seen.add(key)
                    existing = bindings.get(value)
                    if existing is None:
                        bindings[value] = prop
                    elif isinstance(existing, list):
                        existing.append(prop)
                    else:
                        # Tuples are introduced only after collection finishes.
                        bindings[value] = [cast('Property', existing), prop]
    for value, candidates in bindings.items():
        if isinstance(candidates, list):
            bindings[value] = tuple(candidates)
    return cast('dict[DataNode, _Candidates]', bindings)
