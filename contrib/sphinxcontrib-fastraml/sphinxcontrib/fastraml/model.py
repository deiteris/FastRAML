"""Reading values off fastraml's effective model.

The model is parsed with `ParseOptions(unwrap=True)`, so it is already the
effective document: traits and resource types applied, inheritance flattened,
URI parameters propagated, `securedBy` resolved per method, and every cycle
marked with a `RecursiveShape` by P9 (docs/16 § 6 describes the same model as
the tree projects it). These helpers only take values out of it; none decides
anything RAML says.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from fastraml import decimal_text

if TYPE_CHECKING:
    from fastraml import BaseShape, DataNode, ScalarFacet


def text(facet: ScalarFacet[Any] | None) -> str | None:
    """A scalar facet's value as text, or `None` where the facet is absent."""
    if facet is None:
        return None
    return scalar(facet.value)


def texts(facets: list[ScalarFacet[Any]] | None) -> list[str]:
    return [value for facet in facets or [] if (value := text(facet)) is not None]


def scalar(value: object) -> str:
    """One facet value as a reader writes it: a pattern as its source, a bound exactly."""
    if isinstance(value, re.Pattern):
        return str(value.pattern)
    if isinstance(value, Fraction):
        return decimal_text(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def plain(value: DataNode | None) -> Any:
    """A user value -- an example, a default, an enum member -- as plain Python data."""
    return None if value is None else value.raw


def target(base: BaseShape) -> BaseShape:
    """What a shape is, through any alias: an alias shares its referent's everything (docs/07 § 3)."""
    seen: set[int] = set()
    while base.alias is not None and base.id not in seen:
        seen.add(base.id)
        base = base.alias
    return base
