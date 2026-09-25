"""A value to show for one input of a request or response, and where it came from.

Only a value fastraml has validated against the exact shape of that input is
ever shown. The candidates, first valid one wins:

1. the author's own, from the directive (an error when it does not validate);
2. the input's own `example`, then its `examples`, then its `default`;
3. an example of a declared type it extends -- `body: Book` is an anonymous
   subtype of `Book` with no example of its own -- if and only if it validates
   against this input. A subtype that narrows its parent rejects the parent's
   example, and then there is none: examples are not inherited, and this does
   not pretend they are (docs/07 § 4).

An example marked `strict: false` is one its author says does not validate, so
it is never a candidate. Nothing is ever made up: with no valid candidate the
caller shows a placeholder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .model import plain, target

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml import BaseShape, Example


@dataclass(frozen=True, slots=True)
class Chosen:
    """A value to show. A wrapper, so that a chosen `null` is not taken for no value at all."""

    value: Any


def choose(base: BaseShape) -> Chosen | None:
    """The first candidate from the input's own values or its supertypes' examples that validates."""
    for candidate in _candidates(base):
        if base.validate(candidate.value) is None:
            return candidate
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


def _candidates(base: BaseShape) -> Iterator[Chosen]:
    own = target(base)
    yield from _examples(own)
    if own.default is not None:
        yield Chosen(plain(own.default))
    # Ancestors' examples are candidates, not inherited values: `choose` checks
    # each against the original, possibly narrower input before showing it.
    seen = {own.id}
    parents = list(reversed(own.inherits))
    while parents:
        parent = target(parents.pop())
        if parent.id in seen:
            continue
        seen.add(parent.id)
        yield from _examples(parent)
        parents.extend(reversed(parent.inherits))


def _examples(base: BaseShape) -> Iterator[Chosen]:
    found: list[Example] = []
    if base.example is not None:
        found.append(base.example)
    if base.examples is not None:
        # `entries()`, never `values` (AGENTS.md).
        found.extend(base.examples.entries().values())
    for example in found:
        strict = example.strict
        if example.data is not None and (strict is None or strict.value):
            yield Chosen(plain(example.data))
