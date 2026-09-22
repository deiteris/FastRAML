"""An ordinary API: paths, parameters, responses, a header, OAuth2 scopes.

Between them, this and `hard` are what the differential gate runs against. This
one exercises the endpoint half and `hard` the type half.
"""

from typing import Annotated

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field

from aiohttp_raml import (
    AuthenticationError,
    Header,
    OAuth2,
    RamlView,
    Responds,
    secured,
)
from aiohttp_raml.security import setup as setup_security


class Book(BaseModel):
    model_config = ConfigDict(extra='forbid')

    isbn: Annotated[str, Field(pattern=r'^\d{13}$', description='ISBN-13')]
    title: str
    pages: Annotated[int, Field(ge=1, le=10000)] = 100


class Error(BaseModel):
    code: int
    message: str


class Books(OAuth2):
    """Books, and who may read or write them."""

    async def authenticate(self, request: web.Request) -> str:
        token = request.headers.get('Authorization', '')
        if not token:
            raise AuthenticationError('Authorization header missing')
        return token

    async def permits(self, request, identity, scopes) -> bool:
        return True


class BooksView(RamlView):
    async def get(
        self,
        title: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Annotated[web.Response, Responds(200, list[Book], 'a page of books')]:
        """Every book, a page at a time."""
        return web.json_response([])

    async def post(self, body: Book) -> Annotated[web.Response, Responds(201, Book, 'the book added')]:
        """Add a book."""
        return web.json_response(body.model_dump(), status=201)


class BookView(RamlView):
    @secured('books', scopes=['read:books'])
    async def get(
        self,
        isbn: Annotated[str, Field(pattern=r'^\d{13}$')],
        /,
        *,
        request_id: Annotated[str | None, Header('X-Request-Id')] = None,
    ) -> Annotated[
        web.Response,
        Responds(200, Book, 'the book'),
        Responds(404, Error, 'no such book'),
    ]:
        """One book."""
        return web.json_response({})


def build_app() -> web.Application:
    app = web.Application()
    setup_security(
        app,
        {
            'books': Books(
                access_token_uri='https://auth.example/token',
                authorization_uri='https://auth.example/authorize',
                grants=['authorization_code'],
                scopes=['read:books', 'write:books'],
            )
        },
    )
    app.router.add_view('/books', BooksView)
    app.router.add_view('/books/{isbn}', BookView)
    return app


app = build_app()

#: What `render` is called with; an aiohttp application carries no metadata.
METADATA = {
    'title': 'Library',
    'version': 'v2',
    'description': 'Books, and what you may do with them.',
    'base_uri': 'https://api.example/v2',
}
