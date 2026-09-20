"""Shared runtime for a generated server.

Copied verbatim into the generated package; nothing here is templated. It holds
the two things every generated route needs and no document states: how to get a
credential out of the request the document's scheme describes, and what to do
when there is not one.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

__all__ = ['Credential', 'Responses', 'SchemeSpec', 'accepts', 'requires', 'unique_items']


class Responses:
    """The statuses one operation documents, and what the document calls each.

    The request side of the contract is enforced before a handler runs: a body
    the document forbids is a 422 and nothing of yours is called. The response
    side cannot work that way, because a response comes out of your own code.
    This is the nearest thing -- somewhere to raise *from*, so that the status
    and the words both come from the document rather than being retyped.

        raise POST_SHELVES.fail(422)
        raise POST_SHELVES.fail(422, 'shelf 7 is full')

    A status the document does not name raises `LookupError` at the point you
    wrote it. That is a mistake in this code rather than an answer to a caller,
    so it is not an `HTTPException`: turning it into one would answer a request
    with a 500 and hide what was actually wrong.

    Nothing obliges you to use it. `HTTPException` still works, and a status
    raised that way is not checked against anything.
    """

    __slots__ = ('documented',)

    def __init__(self, documented: Mapping[HTTPStatus, str]) -> None:
        self.documented = dict(documented)

    def __contains__(self, status: int) -> bool:
        return status in self.documented

    def fail(self, status: int, detail: str | None = None, **extra: Any) -> HTTPException:
        """The exception for one documented status. `raise` it.

        Returns rather than raises, so that `raise ....fail(...)` reads as a
        raise and a reader can see where control leaves.
        """
        described = self.documented.get(HTTPStatus(status))
        if described is None:
            raise LookupError(
                f'{status} is not one of the statuses this operation documents '
                f'({", ".join(str(int(one)) for one in sorted(self.documented))}). '
                'Raise HTTPException directly if that is deliberate, or add it '
                'to the RAML and regenerate.'
            )
        return HTTPException(status_code=int(status), detail=detail if detail is not None else described, **extra)


@dataclass(frozen=True, slots=True)
class SchemeSpec:
    """One `securitySchemes:` entry, as the mechanics of reading a credential.

    The document says where the credential goes. A `Pass Through` or `x-` scheme
    names its own header through `describedBy:`, so a server that only ever read
    `Authorization: Bearer` would be looking where the caller is not writing.
    """

    name: str
    type: str
    header_name: str
    prefix: str
    scopes: tuple[str, ...] = ()

    def read(self, request: Request) -> str | None:
        """The credential this scheme carries, or nothing.

        A scheme with no prefix takes the header value whole. One with a prefix
        takes what follows it, and a header that does not start with the prefix
        is not this scheme's -- another scheme on the same operation may claim
        it.
        """
        sent = request.headers.get(self.header_name)
        if not sent:
            return None
        if not self.prefix:
            return sent
        head, _, rest = sent.partition(' ')
        return rest.strip() if head.lower() == self.prefix.lower() and rest.strip() else None


@dataclass(frozen=True, slots=True)
class Credential:
    """What the caller sent, and the scheme it arrived under.

    Verifying it is yours to do. The document says an operation is secured and
    which scopes it narrows to; it does not say what a valid token looks like,
    so nothing here decides that.
    """

    scheme: SchemeSpec
    token: str
    #: The scopes the operation requires, from its own `securedBy:`.
    scopes: tuple[str, ...] = ()

    @property
    def basic(self) -> tuple[str, str] | None:
        """The user and password, where the scheme is Basic Authentication.

        `None` for any other scheme, and for a value that is not the base64 of
        `user:password` -- which is a malformed credential rather than a wrong
        one, and still yours to refuse.
        """
        if self.scheme.type != 'Basic Authentication':
            return None
        try:
            decoded = base64.b64decode(self.token, validate=True).decode('utf-8')
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        user, separator, password = decoded.partition(':')
        return (user, password) if separator else None


def requires(*schemes: SchemeSpec, scopes: Sequence[str] = ()) -> Callable[[Request], Credential]:
    """A dependency that yields the credential, or refuses the request.

    **401 is a convention, not a rule the document states.** RAML says an
    operation is secured; it does not say what to answer a caller who sends
    nothing. This answers what RFC 7235 asks for, including the
    `WWW-Authenticate` header, which a 401 is required to carry.
    """

    def dependency(request: Request) -> Credential:
        found = _first(request, schemes, scopes)
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f'this operation is secured by {_named(schemes)}',
                headers={'WWW-Authenticate': _challenge(schemes)},
            )
        return found

    return dependency


def accepts(*schemes: SchemeSpec, scopes: Sequence[str] = ()) -> Callable[[Request], Credential | None]:
    """A dependency that yields the credential where there is one.

    For `securedBy: [null, oauth2]`, which says the call may be made either way.
    """

    def dependency(request: Request) -> Credential | None:
        return _first(request, schemes, scopes)

    return dependency


def unique_items(values: list[Any]) -> list[Any]:
    """Refuse a list that repeats itself.

    pydantic has no `uniqueItems`, and a `set` would be the wrong type: RAML's
    arrays are ordered and their items need not be hashable.
    """
    seen: list[Any] = []
    for value in values:
        if value in seen:
            raise ValueError('items must be unique')
        seen.append(value)
    return values


def _first(request: Request, schemes: Sequence[SchemeSpec], scopes: Sequence[str]) -> Credential | None:
    """The first scheme that finds its credential in this request.

    Schemes are tried in the order the document declared them for the
    operation, so a caller sending two is read as the document reads them.
    """
    for scheme in schemes:
        token = scheme.read(request)
        if token is not None:
            return Credential(scheme=scheme, token=token, scopes=tuple(scopes))
    return None


def _named(schemes: Sequence[SchemeSpec]) -> str:
    return ' or '.join(scheme.name for scheme in schemes) or 'a scheme this server does not name'


def _challenge(schemes: Sequence[SchemeSpec]) -> str:
    """What to put in `WWW-Authenticate`, from the first scheme with a prefix."""
    for scheme in schemes:
        if scheme.prefix:
            return scheme.prefix
    return 'Bearer'
