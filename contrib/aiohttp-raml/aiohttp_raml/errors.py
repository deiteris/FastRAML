"""The 400, 401 and 403 this package answers with, as declared types.

Every operation that takes a parameter can answer 400, so every such operation
declares one -- otherwise the document describes a response the API demonstrably
produces and does not mention it.

`RequestError` is exactly four fields. pydantic's `errors()` also carries
`input` and `ctx`, and neither is written: `input` echoes request data back, and
`ctx` varies by constraint so no single type describes it. What they add is
already in `msg`.

A secured operation declares the 401 and 403 its middleware answers, each a
`Refused`, for the same reason. A handler that declares its own 400, 401 or 403
keeps it; nothing is added over the top.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

__all__ = ['STATUS', 'Refused', 'RequestError', 'describe']

#: The status code an invalid request is answered with.
STATUS = 400


class RequestError(BaseModel):
    """One thing wrong with a request."""

    model_config = ConfigDict(populate_by_name=True)

    #: Which RAML node the value came from: `uriParameters`, `queryParameters`,
    #: `headers` or `body`.
    in_: Annotated[str, Field(alias='in', description='the RAML node the value came from')]
    # No defaults: `describe` writes every key, and a placeholder default here
    # would reach the document as `required: false` and `default: ''`.
    #: The path to the value inside that node.
    loc: list[str | int]
    #: pydantic's error code, e.g. `missing`, `string_pattern_mismatch`.
    type: str
    msg: str


def describe(
    node: str, message: str, *, kind: str = 'value_error', loc: list[str | int] | None = None
) -> dict[str, object]:
    """One `RequestError` as the dict that goes on the wire."""
    return {'in': node, 'loc': loc or [], 'type': kind, 'msg': message}


class Refused(BaseModel):
    """Why a secured request was not let through.

    What `security.middleware` answers: 401 when no scheme authenticates the
    caller, 403 when the one that does will not permit the scopes asked for.
    """

    #: `Authentication required` or `Permission denied`.
    error: str
    #: The reason the scheme gave.
    detail: str
