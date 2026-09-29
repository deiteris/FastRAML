"""A handler's responses: each one its return annotation declares, and the ones its security adds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from raml_document import Body, Response
from raml_document.from_pydantic import Shape

from aiohttp_raml.errors import Refused
from aiohttp_raml.security import FORBIDDEN, UNAUTHENTICATED

if TYPE_CHECKING:
    from raml_document import Method
    from raml_document.from_pydantic import Walk

    from aiohttp_raml.decorator import Described

__all__ = ['refusals', 'responses']


def responses(entry: Described, method: Method, at: str, walk: Walk) -> None:
    """Each declared response, its body as the handler writes it (`Responds.shape`)."""
    for declared in entry.bound.declared_responses:
        response = Response(description=declared.description)
        if declared.body is not None:
            with walk.output(declared.shape):
                response.body = Body({declared.media: walk.annotation(declared.body, f'{at}.{declared.code}')})
        method.responses[declared.code] = response


def refusals(method: Method, at: str, walk: Walk) -> None:
    """The 401 and 403 `security.middleware` answers a secured method with.

    Added as the 400 is: the handler did not write them and the app answers
    them. A status the handler declared itself is left as it said. Read off
    the `securedBy` written, so a scheme left out of it adds nothing, and a
    method that also answers anonymously refuses no one.
    """
    if not method.secured_by or any(entry.scheme is None for entry in method.secured_by):
        return
    for code, description in (
        (UNAUTHENTICATED, 'no security scheme authenticated the request'),
        (FORBIDDEN, 'the scheme that authenticated the request does not permit it'),
    ):
        if code not in method.responses:
            with walk.output(Shape(by_alias=False)):  # `middleware` writes `model_dump()`
                body = Body({'application/json': walk.annotation(Refused, f'{at}.{code}')})
            method.responses[code] = Response(description=description, body=body)
