"""One severity ordering, for every view that grades something.

Two views grade, and they grade on **different axes**. `lint` asks how much a
finding should block CI — `error`, `warning`, `info`. `diff` asks what a change
does to a caller — `breaking`, `risky`, `safe`, `cosmetic`. Those are not two
spellings of one scale and are deliberately not merged: `safe` is not `info`,
and `diff` reports non-problems on purpose because its output is a complete
description of what changed, where a lint report is a list of defects
(docs/18 § 1).

What they do share is the arithmetic. Both need *worst first*, both need "this
one and everything worse", and both had their own copy — a tuple with `.index()`
in one and a dict in the other. A `Ranking` is that arithmetic, given the
vocabulary as data, so a third grading view inherits it and the two that exist
cannot drift.

Nothing here decides a RAML rule, and nothing here knows what any particular
severity *means*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ['Ranking']


class Ranking[S: str]:
    """A severity vocabulary, worst first, and the comparisons it supports.

    Generic over the severity type so `lint`'s `StrEnum` and `diff`'s `Literal`
    both keep their own static type through it. Nothing is validated at
    construction beyond order: a value this ranking has never heard of is a
    programming error at the call site, and `rank` raises rather than guessing
    a position for it.
    """

    __slots__ = ('_rank', 'order')

    def __init__(self, order: Sequence[S]) -> None:
        #: Worst first. This *is* the vocabulary; nothing else enumerates it.
        self.order: tuple[S, ...] = tuple(order)
        self._rank: dict[S, int] = {value: index for index, value in enumerate(self.order)}

    def __repr__(self) -> str:
        return f'Ranking({list(self.order)!r})'

    def __contains__(self, severity: object) -> bool:
        return severity in self._rank

    def rank(self, severity: S) -> int:
        """Position in the order, 0 being worst. Raises on an unknown value."""
        return self._rank[severity]

    def worst(self, severities: Iterable[S]) -> S | None:
        """The most severe of `severities`, or `None` for none at all.

        Used where one thing is graded by several judgements and the kindest
        would mislead: a change reaching both sides of the wire is reported at
        the severity of the worse side, and a rule's findings are grouped under
        the worst of them.
        """
        found = list(severities)
        return min(found, key=self._rank.__getitem__) if found else None

    def at_least(self, severity: S) -> frozenset[S]:
        """`severity` and everything worse — what a `--severity` flag selects.

        A **threshold**, not a membership test — on every verb that filters by
        severity, so one flag name cannot mean opposite things across two verbs
        of one tool (docs/13 § 8).
        """
        limit = self._rank[severity]
        return frozenset(value for value, rank in self._rank.items() if rank <= limit)


#: Each vocabulary is declared by the view that grades on it — `lint`'s three
#: and `diff`'s four — because the *meaning* is that view's and only the
#: arithmetic is shared. Listing them here would put two unrelated scales in one
#: place and invite the assumption that they line up.
