"""The 400 this package returns, as a declared type.

Every operation that takes a parameter can answer 400, so every such operation
declares one -- otherwise the document describes a response the API demonstrably
produces and does not mention it.

The payload is exactly these four fields. pydantic's `errors()` also carries
`input` and `ctx`, and neither is written: `input` echoes request data back, and
`ctx` varies by constraint so no single type describes it. What they add is
already in `msg`.

A handler that declares its own 400 keeps it; nothing is added over the top.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

__all__ = ['STATUS', 'RequestError', 'describe']

#: The status code an invalid request is answered with.
STATUS = 400


class RequestError(BaseModel):
    """One thing wrong with a request."""

    model_config = ConfigDict(populate_by_name=True)

    #: Which RAML node the value came from: `uriParameters`, `queryParameters`,
    #: `headers` or `body`.
    in_: Annotated[str, Field(alias='in', description='the RAML node the value came from')]
    #: The path to the value inside that node.
    loc: list[str | int] = []
    #: pydantic's error code, e.g. `missing`, `string_pattern_mismatch`.
    type: str = ''
    msg: str = ''


def describe(
    node: str, message: str, *, kind: str = 'value_error', loc: list[str | int] | None = None
) -> dict[str, object]:
    """One `RequestError` as the dict that goes on the wire."""
    return {'in': node, 'loc': loc or [], 'type': kind, 'msg': message}
