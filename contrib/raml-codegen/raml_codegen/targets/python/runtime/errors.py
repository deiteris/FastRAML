"""Errors a generated client raises.

Copied verbatim into the generated package; nothing here is templated.
"""

from __future__ import annotations

__all__ = ['ClientError', 'UnexpectedPayload', 'UnexpectedStatus']


class ClientError(Exception):
    """Base of everything this client raises on its own account."""


class UnexpectedPayload(ClientError):
    """A response left out a property the document says is required.

    **Only raised under `Client(strict=True)`.** By default this does not
    happen: the discrepancy is recorded on `Response.mismatches`, the attribute
    holds `UNSET`, and everything that did arrive still arrives. A client that
    raised here would be a client that breaks because the *server* changed, and
    it would take the whole response with it -- including the properties the
    caller actually wanted, which are usually all of them.

    Strict is for a caller that would rather stop than proceed on a payload the
    document does not describe.
    """

    def __init__(self, model: str, field: str, payload: object = None) -> None:
        self.model = model
        self.field = field
        self.payload = payload
        super().__init__(f'{model} requires {field!r}, which the payload does not carry')


class UnexpectedStatus(ClientError):
    """The server answered with a status the document does not describe.

    Raised only when the client is built with `raise_on_unexpected_status=True`.
    The default is to return `None` from the parsing entry points and leave the
    raw response reachable through the detailed ones.
    """

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content
        super().__init__(f'unexpected status {status_code}: {content.decode(errors="replace")[:512]}')
