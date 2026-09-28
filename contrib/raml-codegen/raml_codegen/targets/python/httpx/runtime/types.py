"""Shared types for a generated client.

Copied verbatim into the generated package; nothing here is templated.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import IO, Any, BinaryIO, Literal, Self

from .errors import UnexpectedPayload

__all__ = [
    'UNSET',
    'DateOnly',
    'DateTime',
    'DateTimeOnly',
    'File',
    'FileTypes',
    'HttpDate',
    'Mismatch',
    'RequestFiles',
    'Response',
    'TimeOnly',
    'Unset',
    'as_dict',
    'as_list',
    'each',
    'reading',
    'require',
]


class Unset:
    """An argument that was not supplied at all.

    Distinct from `None`, which is a value a document may declare: `?param=` and
    a parameter left off the call are different requests. A model has no use for
    it: a key the payload does not carry is not in the dictionary.
    """

    _instance: Self | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return 'UNSET'


UNSET: Unset = Unset()

# A date is the string the server sent. Each kind is named so a signature says
# which string it is; parsing one is the caller's, with the kind in hand.

DateTime = str
"""An RFC 3339 date-time with an offset, `2024-01-01T00:00:00Z`: `datetime.fromisoformat`."""

HttpDate = str
"""An RFC 2616 date-time, `Sun, 06 Nov 1994 08:49:37 GMT`: `email.utils.parsedate_to_datetime`."""

DateTimeOnly = str
"""A date-time with no offset, `2024-01-01T00:00:00`: `datetime.fromisoformat`."""

DateOnly = str
"""A date, `2024-01-01`: `date.fromisoformat`."""

TimeOnly = str
"""A time of day, `00:00:00`: `time.fromisoformat`."""

FileContent = IO[bytes] | bytes | str
FileTypes = tuple[str | None, FileContent, str | None] | tuple[str | None, FileContent, str | None, Mapping[str, str]]
RequestFiles = list[tuple[str, FileTypes]]


@dataclass(slots=True)
class File:
    """A file upload."""

    payload: BinaryIO
    file_name: str | None = None
    mime_type: str | None = None

    def to_tuple(self) -> FileTypes:
        """The form `httpx` accepts for multipart/form-data."""
        return self.file_name, self.payload, self.mime_type


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One place a response did not match the document it was generated from."""

    model: str
    field: str
    reason: str = 'the document requires this property and the payload does not carry it'

    def __str__(self) -> str:
        where = f'{self.model}.{self.field}' if self.field else self.model
        return f'{where}: {self.reason}'


@dataclass(slots=True)
class Response[T]:
    """One response, with the parsed body beside the raw one.

    `mismatches` is empty when the payload was what the document described. When
    it is not, `parsed` is still the whole payload, and `content` still holds the
    bytes -- nothing is thrown away because one property was missing.
    """

    status_code: HTTPStatus
    content: bytes
    headers: MutableMapping[str, str]
    parsed: T | None
    mismatches: tuple[Mismatch, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        """True when the payload was exactly what the document described."""
        return not self.mismatches


#: Set for the duration of one response read. Outside one -- somebody calling
#: a model's reader directly -- nothing is collected and nothing is raised.
_collecting: contextvars.ContextVar[list[Mismatch] | None] = contextvars.ContextVar('_collecting', default=None)
_strict: contextvars.ContextVar[bool] = contextvars.ContextVar('_strict', default=False)


@contextlib.contextmanager
def reading(*, strict: bool = False) -> Iterator[list[Mismatch]]:
    """Collect what one payload did not match, instead of failing on the first."""
    found: list[Mismatch] = []
    collecting = _collecting.set(found)
    strictly = _strict.set(strict)
    try:
        yield found
    finally:
        _collecting.reset(collecting)
        _strict.reset(strictly)


def require(values: Mapping[str, Any], model: str, keys: tuple[str, ...]) -> None:
    """Record each of `keys` that `values` does not carry, and carry on.

    Raising here would be the client breaking because the *server* changed, and
    would take the whole response with it -- including every property that did
    arrive, which is usually all of them and usually all the caller wanted. The
    discrepancy reaches `Response.mismatches` instead, and the payload is handed
    back as it arrived: read a key it may lack with `.get`.

    `Client(strict=True)` turns it back into an exception for a caller that
    would rather not proceed on a payload the document does not describe.
    """
    for key in keys:
        if key in values:
            continue
        found = _collecting.get()
        if found is not None:
            found.append(Mismatch(model, key))
        if _strict.get():
            raise UnexpectedPayload(model, key, None)


def as_dict(value: Any) -> dict[str, Any]:
    """A JSON object, or a mismatch.

    A reader hands back what it was given, typed as the model it checked. A
    string typed as a `Book` would be the client vouching for a shape the
    payload does not have, so a value that is not an object reaches the
    backstop in `_build` instead, which records it and returns no body.
    """
    if isinstance(value, dict):
        return value
    raise TypeError(f'expected an object, got {type(value).__name__}')


def as_list(value: Any) -> list[Any]:
    """A JSON array, or a mismatch.

    Iterating the wrong thing is worse than failing to: a `dict` where an array
    was promised yields its *keys*, and each would be checked as a model. Raising
    here reaches the backstop in `_build`, which records it and returns no body.
    """
    if isinstance(value, list):
        return value
    raise TypeError(f'expected an array, got {type(value).__name__}')


def each(checks: Iterable[object]) -> None:
    """Run one check per item of an array, for what the checks report."""
    for _ in checks:
        pass
