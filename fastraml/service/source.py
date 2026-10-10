"""On-demand composed trees shared by current-text and snapshot consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastraml.errors import RamlError
from fastraml.yamlnode import NodeKind, backend_name, compose

if TYPE_CHECKING:
    from fastraml.yamlnode import Node

_BOM = chr(0xFEFF)


def original_tree(uri: str, root: Node | None) -> Node | None:
    """Only unchanged YAML/RAML containers have the standalone composition policy.

    JSON includes can normalize whitespace; non-YAML includes and external
    schemas can use synthetic scalars. Neither is a generic composed tree.
    """
    path = uri.split('#', 1)[0].split('?', 1)[0]
    if root is not None and root.kind is not NodeKind.SCALAR and path.lower().endswith(('.raml', '.yaml', '.yml')):
        return root
    return None


@dataclass(slots=True, eq=False)
class _Entry:
    text: str
    max_depth: int
    backend: str
    root: Node | None


class Sources:
    """Workspace-owned trees; snapshots borrow this owner rather than retain it."""

    __slots__ = ('__weakref__', '_changed', '_entries', '_policy_generation', 'backend', 'generation', 'max_depth')

    def __init__(self, max_depth: int) -> None:
        self._entries: dict[str, _Entry] = {}
        self._changed: dict[str, int] = {}
        self._policy_generation = 0
        self.max_depth = max_depth
        self.backend = backend_name()
        self.generation = 0

    def configure(self, max_depth: int) -> None:
        """The current owner's composition policy invalidates older publications."""
        backend = backend_name()
        if (max_depth, backend) != (self.max_depth, self.backend):
            self._entries.clear()
            self.generation += 1
            self._policy_generation = self.generation
            self.max_depth, self.backend = max_depth, backend

    def discard(self, uri: str, *, read: bool) -> None:
        cached = self._entries.pop(uri, None)
        self.generation += 1
        # Watchers see unrelated files too. Remember changes only for source
        # a snapshot/cache has used, including any already tracked old reader.
        if read or cached is not None or uri in self._changed:
            self._changed[uri] = self.generation

    def lookup(self, uri: str, text: str, max_depth: int) -> _Entry | None:
        """An equal parsed input/policy can share a successful or failed result."""
        entry = self._entries.get(uri)
        if (
            entry is not None
            and entry.text == text.removeprefix(_BOM)
            and entry.max_depth == max_depth
            and entry.backend == backend_name()
        ):
            return entry
        return None

    def node(
        self,
        uri: str,
        text: str,
        *,
        max_depth: int,
        generation: int | None = None,
        original: Node | None = None,
    ) -> Node | None:
        """Compose on a miss; an old snapshot cannot replace the current owner."""
        entry = self.lookup(uri, text, max_depth)
        text = text.removeprefix(_BOM)
        backend = backend_name()
        current = (
            (generation is None or generation >= max(self._changed.get(uri, 0), self._policy_generation))
            and max_depth == self.max_depth
            and backend == self.backend
        )
        if entry is not None and not (original is not None and current):
            return entry.root
        root = original
        if root is None:
            try:
                root = compose(text, uri=uri, max_depth=max_depth)
            except RamlError:
                root = None
        if current:
            self._entries[uri] = _Entry(text, max_depth, backend, root)
        return root
