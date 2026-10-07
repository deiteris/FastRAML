"""Inline inferred/expected types and inherited facets (docs/21 § 4.4)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Final

from fastraml.types.complex_ import ArrayShape, RecursiveShape, UnionShape
from fastraml.types.jsonschema_ import projected
from fastraml.types.values import decimal_text
from fastraml.views.render import type_name

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.positions import Position
    from fastraml.service.workspace import Snapshot
    from fastraml.types.base import BaseShape


@dataclass(frozen=True, slots=True, eq=False)
class Part:
    label: str
    definition_uri: str | None = None
    definition_span: Position | None = None
    tooltip: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Hint:
    position: Position
    parts: tuple[Part, ...]
    tooltip: str
    note: str | None = None

    @property
    def label(self) -> str:
        return ''.join(part.label for part in self.parts)


LABEL_LIMIT: Final = 32
_TICKS = re.compile(r'`+')
_BOUNDS = (
    ('minLength', 'maxLength', 'length'),
    ('minItems', 'maxItems', 'items'),
    ('minProperties', 'maxProperties', 'properties'),
    ('minimum', 'maximum', 'range'),
)


def type_label(name: str) -> str:
    """A bounded type name, never a serialized facet/value preview."""
    return name if len(name) <= LABEL_LIMIT else name[: LABEL_LIMIT - 1] + '…'


def underlying_type(base: BaseShape) -> str:
    """Name the effective kind, rather than repeating the authored reference."""
    view = projected(base.shape.head if isinstance(base.shape, RecursiveShape) else base)
    if isinstance(view.shape, UnionShape) and view.shape.any_of:
        return ' | '.join(type_name(member, nested=True) for member in view.shape.any_of)
    if isinstance(view.shape, ArrayShape) and view.shape.items is not None:
        member = type_name(view.shape.items)
        return f'({member})[]' if ' | ' in member else f'{member}[]'
    return view.type or 'any'


def facet_value(value: object) -> str:
    if isinstance(value, Fraction):
        return decimal_text(value)
    pattern = getattr(value, 'pattern', None)
    return json.dumps(pattern if isinstance(pattern, str) else value, ensure_ascii=False, default=str)


def constraint_summary(values: Mapping[str, object], *, limit: int) -> str | None:
    """Whole, concrete facts that fit beside a reference; never a vague badge."""
    parts = []
    used: set[str] = set()
    for lower, upper, label in _BOUNDS:
        low, high = values.get(lower), values.get(upper)
        if low is None and high is None:
            continue
        used.update((lower, upper))
        if low is not None and high is not None:
            bound = facet_value(low) if low == high else f'{facet_value(low)}..{facet_value(high)}'
        elif low is not None:
            bound = f'≥{facet_value(low)}'
        else:
            bound = f'≤{facet_value(high)}'
        parts.append(f'{label}: {bound}')
    parts.extend(f'{name}: {facet_value(value)}' for name, value in values.items() if name not in used)
    fitting: list[str] = []
    for part in parts:
        if len('; '.join((*fitting, part))) <= limit:
            fitting.append(part)
    return '; '.join(fitting) or None


def constraint_details(values: Mapping[str, object]) -> str:
    text = '\n'.join(f'{name}: {facet_value(value)}' for name, value in values.items())
    fence = '`' * max(3, max((len(run) for run in _TICKS.findall(text)), default=0) + 1)
    return f'**Inherited constraints**\n\n{fence}yaml\n{text}\n{fence}'


def inlay_hints(snapshot: Snapshot, uri: str, span: Position) -> list[Hint]:
    context = snapshot.hover
    return [] if context is None else context.inlay_hints(uri, span)
