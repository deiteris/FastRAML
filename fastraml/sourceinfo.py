"""Facts a decoder records when it accepts a compatibility spelling.

A `KeywordUse` names where a deprecated keyword was written. It is a model
fact the registry stores and a view may report on: not a diagnostic, and it
keeps no YAML node (docs/04 § 5, docs/05 § 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastraml.positions import Position


@dataclass(frozen=True, slots=True, eq=False)
class KeywordUse:
    """An accepted compatibility spelling, not a lint finding or retained tree."""

    location: str
    position: Position
    name: str
