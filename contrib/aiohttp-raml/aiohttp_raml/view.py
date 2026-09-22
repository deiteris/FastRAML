"""`RamlView`: a class-based handler whose methods describe themselves.

    class BookView(RamlView):
        async def get(self, isbn: str, /) -> Annotated[
            web.Response,
            Responds(200, Book, 'the book'),
            Responds(404, Error, 'no such book'),
        ]:
            \"\"\"One book.\"\"\"
            ...

    app.router.add_view('/books/{isbn}', BookView)

`__init_subclass__` finds the HTTP methods the class defines and wraps each with
`validate.in_method`, so a subclass needs no decorator per method. A method
defined on a *parent* is left alone -- it was wrapped when that class was
created, and wrapping twice would validate twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from aiohttp.abc import AbstractView
from aiohttp.hdrs import METH_ALL
from aiohttp.web_exceptions import HTTPMethodNotAllowed

from aiohttp_raml.decorator import validate

if TYPE_CHECKING:
    from collections.abc import Generator

    from aiohttp.web_response import StreamResponse

__all__ = ['RamlView', 'is_raml_view']


class RamlView(AbstractView):
    """An aiohttp view whose methods are validated against their annotations."""

    #: The verbs this class answers, filled by `__init_subclass__`.
    allowed_methods: ClassVar[frozenset[str]] = frozenset()

    #: Set per subclass to check each response against what it declared. Costs a
    #: JSON parse and a validation per response, so it is off by default and
    #: meant for a test or development run.
    check_responses: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.allowed_methods = frozenset(verb for verb in METH_ALL if hasattr(cls, verb.lower()))
        wrap = validate.in_method(check_responses=cls.check_responses)
        for verb in METH_ALL:
            name = verb.lower()
            if name in vars(cls):
                setattr(cls, name, wrap(getattr(cls, name)))

    async def _iter(self) -> StreamResponse:
        verb = self.request.method
        if verb not in self.allowed_methods:
            raise HTTPMethodNotAllowed(verb, sorted(self.allowed_methods))
        return await getattr(self, verb.lower())()  # type: ignore[no-any-return]

    def __await__(self) -> Generator[Any, None, StreamResponse]:
        return self._iter().__await__()


def is_raml_view(obj: Any) -> bool:
    try:
        return issubclass(obj, RamlView)
    except TypeError:
        return False
