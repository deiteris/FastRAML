"""Deferring full garbage collections while fastRAML builds a model (docs/12 § 6).

Parsing, linting and graph building grow a large set of long-lived objects
and free almost none of them in a cycle. CPython's full (generation 2)
collection re-scans every tracked object each time it runs, so the number of
full collections times a heap that keeps growing makes a parse superlinear
in its input. Young collections scan only new objects and stay linear.

`tuned_gc` raises the generation 2 threshold for the length of one operation
and restores it afterwards; the young thresholds are left alone, so cyclic
garbage is still collected at the usual rate. Thresholds are process-wide,
which is why the rules below exist:

* only ever raise: a host threshold already at or above ours is kept;
* nothing happens when the host has disabled the collector;
* nested and concurrent operations share one change: the first to enter
  saves the host's thresholds and the last to leave restores them;
* a threshold the host changed in the meantime is left as the host set it;
* `set_gc_tuning(False)` makes every later operation leave the collector
  alone.
"""

from __future__ import annotations

import gc
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['set_gc_tuning', 'tuned_gc']

#: The generation 2 threshold while an operation runs: a full collection then
#: needs a thousand generation 1 collections, which no parse reaches.
FULL_COLLECTION_THRESHOLD: Final = 1000

_lock = threading.Lock()
_enabled = True
_depth = 0
#: The host's thresholds, and the ones we set, while `_depth > 0`; `None`
#: when the first operation to enter found nothing to change.
_saved: tuple[int, int, int] | None = None
_ours: tuple[int, int, int] | None = None


def set_gc_tuning(enabled: bool) -> None:  # noqa: FBT001 - a switch
    """Let fastRAML defer full collections during its operations, or not.

    Process-wide, because the collector's thresholds are. An operation
    already running when tuning is turned off still restores what it changed.
    """
    global _enabled  # noqa: PLW0603 - one switch per process
    _enabled = enabled


@contextmanager
def tuned_gc() -> Iterator[None]:
    """Run the enclosed operation with full collections deferred."""
    if not _enabled:
        yield
        return
    _enter()
    try:
        yield
    finally:
        _leave()


def _enter() -> None:
    global _depth, _saved, _ours  # noqa: PLW0603 - shared by every thread
    with _lock:
        if _depth == 0:
            current = gc.get_threshold()
            if gc.isenabled() and current[2] < FULL_COLLECTION_THRESHOLD:
                tuned = (current[0], current[1], FULL_COLLECTION_THRESHOLD)
                gc.set_threshold(*tuned)
                _saved, _ours = current, tuned
        _depth += 1


def _leave() -> None:
    global _depth, _saved, _ours  # noqa: PLW0603 - shared by every thread
    with _lock:
        _depth -= 1
        if _depth or _saved is None:
            return
        if gc.get_threshold() == _ours:
            gc.set_threshold(*_saved)
        _saved = _ours = None
