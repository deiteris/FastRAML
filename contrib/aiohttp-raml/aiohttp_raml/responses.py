"""What a handler responds with, declared on the return annotation.

    async def get(self, isbn: str, /) -> Annotated[
        web.Response,
        Responds(200, Book, 'the book'),
        Responds(404, Error, 'no such book'),
    ]: ...

The return type stays `web.Response`, because that is what the handler returns.
A type checker sees `web.Response` and is satisfied; the metadata is for this
package.

**Nothing here is checked statically, in any spelling.** `web.json_response`
takes `Any`, so the payload's type is gone by the time the handler returns --
no arrangement of annotations recovers it. `decorator.validate(check_responses=True)`
is what closes that gap, at runtime and by choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, get_args, get_type_hints

__all__ = ['Responds', 'read_responses']

_LOWEST: Final = 100
_HIGHEST: Final = 599


@dataclass(frozen=True, slots=True)
class Responds:
    """One entry of RAML's `responses:`.

    `body` is a type annotation, so `list[Book]` and `Book | None` work as they
    do anywhere else. `None` declares a response with no body.
    """

    code: int
    body: Any = None
    description: str | None = None
    media: str = 'application/json'

    def __post_init__(self) -> None:
        if not (_LOWEST <= self.code <= _HIGHEST):
            raise ValueError(f'{self.code} is not an HTTP status code')


def read_responses(handler: Any, hints: dict[str, Any] | None = None) -> list[Responds]:
    """Every `Responds` on a handler's return annotation, in declaration order.

    A handler that declares none gets none. RAML permits a method with no
    `responses:`, and inventing a 200 would be this package deciding something
    the handler did not say.
    """
    if hints is None:
        hints = get_type_hints(handler, include_extras=True)
    annotation = hints.get('return')
    if annotation is None or not hasattr(annotation, '__metadata__'):
        return []
    found = [item for item in get_args(annotation)[1:] if isinstance(item, Responds)]
    _check_distinct(handler, found)
    return found


def _check_distinct(handler: Any, found: list[Responds]) -> None:
    seen: set[int] = set()
    for item in found:
        if item.code in seen:
            name = getattr(handler, '__qualname__', repr(handler))
            raise TypeError(f'{name}: status code {item.code} is declared twice')
        seen.add(item.code)
