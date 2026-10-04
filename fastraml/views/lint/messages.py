"""The messages of one operation, for rules that read each alike."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastraml.parser.endpoints import Operation, Request, Response

__all__ = ['messages']


def messages(operation: Operation) -> Iterator[tuple[str, Request | Response]]:
    """The request, if declared, then each response, in declaration order, each
    named by where it sits: `request` or the status code.
    """
    if operation.request is not None:
        yield 'request', operation.request
    yield from operation.responses.items()
