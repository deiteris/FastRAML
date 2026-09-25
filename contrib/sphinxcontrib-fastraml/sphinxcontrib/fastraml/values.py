"""A value to show for one input of a request or response, and where it came from.

Only a value fastraml has validated against the exact shape of that input is
ever shown. The candidates, first valid one wins:

1. the author's own, from the directive (an error when it does not validate);
2. `fastraml.sample` with synthesis off (docs/16 § 8.1): the input's own
   examples, never one marked `strict: false`, then its `default`, then an
   `enum` member; else a value composed from its properties' or items' own.

A supertype's example is never a candidate. `body: {type: Book}` is a subtype
of `Book` that may narrow it, so its value is composed from Book's properties
instead; `body: Book` is Book itself and carries its examples (docs/07 § 3).
Nothing is ever made up: with no candidate the caller shows a placeholder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastraml import SampleError, SampleOptions, sample

if TYPE_CHECKING:
    from fastraml import BaseShape

#: Composed from the author's data alone: nothing is ever made up.
_DECLARED_ONLY = SampleOptions(synthesize=False)


@dataclass(frozen=True, slots=True)
class Chosen:
    """A value to show. A wrapper, so that a chosen `null` is not taken for no value at all."""

    value: Any


def choose(base: BaseShape) -> Chosen | None:
    """The input's declared value, or one composed from declared values; else `None`."""
    try:
        return Chosen(sample(base, options=_DECLARED_ONLY))
    except SampleError:
        return None


def supplied(base: BaseShape, written: Any) -> tuple[Chosen | None, str | None]:
    """The author's value, if fastraml accepts it for this input; else why not."""
    error = base.validate(written)
    if error is None:
        return Chosen(written), None
    return None, '; '.join(error.messages())


def supplied_text(base: BaseShape, written: str) -> tuple[Chosen | None, str | None]:
    """A parameter value as written in a directive: as text, else as JSON.

    Parameters travel as text, and fastraml validates them as such -- `20`
    for an integer is fine either way. The JSON reading is what lets an author
    write `true`, `[1, 2]` or a quoted string with spaces.
    """
    chosen, error = supplied(base, written)
    if chosen is not None:
        return chosen, None
    try:
        parsed = json.loads(written)
    except ValueError:
        return None, error
    return supplied(base, parsed)
