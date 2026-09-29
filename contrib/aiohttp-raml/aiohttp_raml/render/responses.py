"""A handler's responses: each one its return annotation declares."""

from __future__ import annotations

from typing import TYPE_CHECKING

from raml_document import Body, Response

if TYPE_CHECKING:
    from raml_document import Method
    from raml_document.from_pydantic import Walk

    from aiohttp_raml.decorator import Described

__all__ = ['responses']


def responses(entry: Described, method: Method, at: str, walk: Walk) -> None:
    for declared in entry.bound.declared_responses:
        response = Response(description=declared.description)
        if declared.body is not None:
            response.body = Body({declared.media: walk.annotation(declared.body, f'{at}.{declared.code}')})
        method.responses[declared.code] = response
