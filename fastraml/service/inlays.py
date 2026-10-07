"""Inline inferred/expected types and inherited facets (docs/21 § 4.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastraml.positions import Position
    from fastraml.service.workspace import Snapshot


@dataclass(frozen=True, slots=True, eq=False)
class Hint:
    position: Position
    label: str
    tooltip: str
    definition_uri: str | None = None
    definition_span: Position | None = None


def inlay_hints(snapshot: Snapshot, uri: str, span: Position) -> list[Hint]:
    context = snapshot.hover
    return [] if context is None else context.inlay_hints(uri, span)
