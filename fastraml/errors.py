"""The diagnostic model.

A RAML file that fails to parse should say what is wrong, where, and how the
parser reached that point. Errors therefore compose in two directions:

* **wrap** pushes a frame onto a chain — the vertical dimension, "how we got
  here". `RamlError.wrap('merge shapes', cause, ...)`.
* **append** records an independent failure alongside an existing one — the
  horizontal dimension, "several things went wrong". `err.append(other)`.

`Accumulator` collects independent failures from passes that tolerate a local
error, so one broken response body does not discard its siblings.

See docs/11-diagnostics.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from fastraml.positions import Position

__all__ = [
    'Accumulator',
    'ErrorKind',
    'RamlError',
    'Trace',
]


class ErrorKind(StrEnum):
    """Which pass produced a diagnostic. Rendered as the `type` field."""

    PARSING = 'parsing'
    READING = 'reading'
    LOADING = 'loading'
    RESOLVING = 'resolving'
    UNWRAPPING = 'unwrapping'
    VALIDATING = 'validating'


class Trace:
    """One frame of a diagnostic chain.

    `info` holds the values that vary between occurrences of the same message.
    Keeping them out of `message` means diagnostics group cleanly and tests can
    match on the message alone. See docs/11-diagnostics.md § 6.
    """

    __slots__ = ('cause', 'info', 'kind', 'location', 'message', 'position')

    def __init__(  # noqa: PLR0913, PLR0917 - a diagnostic frame carries six fields
        self,
        message: str,
        location: str,
        position: Position | None = None,
        kind: ErrorKind = ErrorKind.PARSING,
        info: Mapping[str, Any] | None = None,
        cause: Trace | None = None,
    ) -> None:
        self.message = message
        self.location = location
        self.position = position
        self.kind = kind
        self.info = info or {}
        self.cause = cause

    def __repr__(self) -> str:
        return f'Trace({self.message!r}, {self.where()!r})'

    def where(self) -> str:
        """`location:line:column`, or just the location when no position is known."""
        if self.position is None:
            return self.location
        return f'{self.location}:{self.position}'

    def rendered_message(self) -> str:
        """The message with its `info` values appended, in insertion order."""
        if not self.info:
            return self.message
        details = ': '.join(f'{k}: {v}' for k, v in self.info.items())
        return f'{self.message}: {details}'

    def to_dict(self) -> dict[str, Any]:
        return {
            'message': self.rendered_message(),
            'position': self.where(),
            'severity': 'error',
            'type': str(self.kind),
        }


class RamlError(Exception):
    """A chain of `Trace` frames, plus any independent failures beside it.

    `head` is the outermost frame; following `Trace.cause` walks inward to the
    root problem. `siblings` holds failures that happened independently of this
    one, which is how a pass reports several broken endpoints at once.

    Instances are treated as immutable: `append` returns a new error rather than
    mutating, so an error handed to two accumulators cannot grow behind one of
    their backs.
    """

    __slots__ = ('head', 'siblings')

    def __init__(self, head: Trace, siblings: Sequence[RamlError] = ()) -> None:
        # No message is passed up: rendering one formats every `info` value,
        # and a union scan builds, then discards, one error per member that
        # fails (`bench micro 'validate union'`). `args` renders on demand.
        super().__init__()
        self.head = head
        self.siblings: tuple[RamlError, ...] = tuple(siblings)

    @property
    def args(self) -> tuple[str]:  # type: ignore[override]  # read-only: nothing assigns it
        """`(message,)`, the head's rendered message, as `Exception.args` would hold it."""
        return (self.head.rendered_message(),)

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.head.rendered_message()!r})'

    def __reduce__(self) -> tuple[type[Self], tuple[Trace, tuple[RamlError, ...]]]:
        """Rebuild from the chain, not from `args`, which is text (docs/11 § 1).

        `Exception`'s own reduce calls the class with `args`, which never fit
        this constructor: an error raised in a worker process came back to
        its parent as a `TypeError`.
        """
        return (type(self), (self.head, self.siblings))

    # -- construction ---------------------------------------------------------

    @classmethod
    def new(
        cls,
        message: str,
        location: str,
        position: Position | None = None,
        *,
        kind: ErrorKind = ErrorKind.PARSING,
        info: Mapping[str, Any] | None = None,
    ) -> Self:
        """Start a new chain."""
        return cls(Trace(message, location, position, kind, info))

    @classmethod
    def wrap(  # noqa: PLR0913 - mirrors `new`, plus the cause
        cls,
        message: str,
        cause: BaseException,
        location: str,
        position: Position | None = None,
        *,
        kind: ErrorKind = ErrorKind.PARSING,
        info: Mapping[str, Any] | None = None,
    ) -> Self:
        """Push a frame onto `cause`, preserving its chain and its siblings.

        A non-`RamlError` cause becomes the innermost frame, carrying the same
        location so that a stray `OSError` still reports a file. Its own `info`
        comes with it where it has one -- `WorkspaceEscapeError` computes the
        root that would have worked, and a value nothing carries forward is a
        value the caller has to recover from prose.
        """
        if isinstance(cause, RamlError):
            inner = cause.head
            siblings = cause.siblings
        else:
            inner = Trace(str(cause), location, position, kind, getattr(cause, 'info', None))
            siblings = ()
        return cls(Trace(message, location, position, kind, info, cause=inner), siblings)

    def append(self, other: RamlError | None) -> RamlError:
        """Return this error with `other` recorded as an independent failure."""
        if other is None:
            return self
        return RamlError(self.head, (*self.siblings, other))

    # -- inspection -----------------------------------------------------------

    def frames(self) -> list[Trace]:
        """The error's own chain, outermost frame first."""
        out: list[Trace] = []
        frame: Trace | None = self.head
        while frame is not None:
            out.append(frame)
            frame = frame.cause
        return out

    def chains(self) -> Iterator[list[Trace]]:
        """Every chain in this error, its own first, then its siblings' (depth first)."""
        yield self.frames()
        for sibling in self.siblings:
            yield from sibling.chains()

    def messages(self) -> list[str]:
        """The innermost message of every chain — the actual problems found."""
        return [chain[-1].rendered_message() for chain in self.chains()]

    # -- rendering ------------------------------------------------------------

    def __str__(self) -> str:
        lines: list[str] = []
        for index, chain in enumerate(self.chains()):
            lines.append(f'[{index}]')
            for depth, frame in enumerate(chain):
                lines.append(f'{"  " * (depth + 1)}{frame.where()} {frame.rendered_message()}')
        return '\n'.join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Return the flattened machine-readable chain projection."""
        return {'traces': [{'stack': [frame.to_dict() for frame in chain]} for chain in self.chains()]}


class Accumulator:
    """Collects independent failures so a pass can report all of them.

    Used by every pass that tolerates a local error. See docs/11-diagnostics.md
    § 2 for which passes those are and at what granularity.
    """

    __slots__ = ('_errors',)

    def __init__(self) -> None:
        self._errors: list[RamlError] = []

    def __bool__(self) -> bool:
        return bool(self._errors)

    def __len__(self) -> int:
        return len(self._errors)

    def add(self, error: RamlError | None) -> None:
        """Record a failure. `None` is ignored, so callers need no guard."""
        if error is not None:
            self._errors.append(error)

    def result(self) -> RamlError | None:
        """All recorded failures as one error, or `None` if there were none.

        A single failure is returned unchanged; several are returned as the
        first with the rest attached as siblings.
        """
        if not self._errors:
            return None
        first, *rest = self._errors
        if not rest:
            return first
        return RamlError(first.head, (*first.siblings, *rest))

    def raise_if_any(self) -> None:
        error = self.result()
        if error is not None:
            raise error
