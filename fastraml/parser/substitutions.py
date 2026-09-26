"""Where a template's substituted value was written (docs/08 § 5.1).

P4 records it while substituting (`parser/templates.py`); the decoders and P7
read it to place a name inside a caller's value where the caller wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastraml.positions import Position
    from fastraml.yamlnode import Node

__all__ = ['Substitution', 'Substitutions', 'substituted_site']


@dataclass(frozen=True, slots=True)
class Substitution:
    """A caller's value inside a substituted scalar, and where it was written.

    `start` and `end` are offsets into the scalar's text. `node` is the value
    as the caller wrote it, in the file `location`.
    """

    start: int
    end: int
    location: str
    node: Node


#: Every scalar a substitution produced, with the caller's values it holds
#: verbatim; empty where it holds none. Read by the decoders and P7, and
#: dropped when P7 ends.
type Substitutions = dict[Node, tuple[Substitution, ...]]


def substituted_site(
    substitutions: Substitutions, node: Node, offset: int, end: int | None = None
) -> tuple[str, Position] | None:
    """Where the characters from `offset` to `end`, or the one at `offset`, in
    `node`'s text were written, if one caller's value holds them all: the file,
    and the caller's own position.
    """
    end = offset + 1 if end is None else end
    for part in substitutions.get(node, ()):
        if part.start <= offset and end <= part.end:
            written = part.node
            return part.location, written.position.within(written.value).shifted(offset - part.start)
    return None
