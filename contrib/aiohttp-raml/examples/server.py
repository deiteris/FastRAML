"""A real API that answers, and describes itself at `/raml`.

    uv run python -m examples.server

| URL | What it is |
|-----|------------|
| `/books`, `/books/{isbn}` | the API itself, over a real dict |
| `/books/{isbn}/cover` | a streamed `multipart/form-data` upload |
| `/raml` | the RAML source, as `application/raml+yaml` |
| `/raml.json` | the same document as `fastraml tree` output |
| `/raml-viewer` | the `fastraml-viewer` bundle, reading this app's tree |

Handlers that answer, not stubs: the description and the validation are the same
annotations, so a request the document accepts is a request the app accepts.
"""

from typing import Annotated

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field

from aiohttp_raml import Documentation, File, RamlView, Responds, UploadedFile, add_raml_routes


class Book(BaseModel):
    model_config = ConfigDict(extra='forbid')

    isbn: Annotated[str, Field(pattern=r'^\d{13}$', description='ISBN-13')]
    title: str
    pages: Annotated[int, Field(ge=1, le=10000)] = 100


class Error(BaseModel):
    code: int
    message: str


BOOKS: dict[str, Book] = {
    '9780306406157': Book(isbn='9780306406157', title='The Hobbit', pages=310),
}
#: isbn -> the size of the cover uploaded for it.
COVERS: dict[str, int] = {}


class BooksView(RamlView):
    # Every response is checked against what it declared. The cost is a JSON
    # parse per response, which is why it is opt-in -- but in an example whose
    # point is that the document and the behaviour agree, it belongs on.
    check_responses = True

    async def get(
        self, title: str | None = None
    ) -> Annotated[web.Response, Responds(200, list[Book], 'every matching book')]:
        """Every book, optionally filtered by title."""
        found = [book for book in BOOKS.values() if title is None or title.lower() in book.title.lower()]
        return web.json_response([book.model_dump() for book in found])

    async def post(self, body: Book) -> Annotated[web.Response, Responds(201, Book, 'the book added')]:
        """Add a book."""
        BOOKS[body.isbn] = body
        return web.json_response(body.model_dump(), status=201)


class BookView(RamlView):
    check_responses = True

    async def get(
        self, isbn: str, /
    ) -> Annotated[
        web.Response,
        Responds(200, Book, 'the book'),
        Responds(404, Error, 'no such book'),
    ]:
        """One book."""
        book = BOOKS.get(isbn)
        if book is None:
            return web.json_response(Error(code=404, message='no such book').model_dump(), status=404)
        return web.json_response(book.model_dump())


class CoverView(RamlView):
    check_responses = True

    async def post(
        self,
        isbn: str,
        /,
        cover: Annotated[UploadedFile, File(file_types=['image/png'], max_size=1_000_000)],
    ) -> Annotated[
        web.Response,
        Responds(201, Book, 'the book, with its cover stored'),
        Responds(404, Error, 'no such book'),
    ]:
        """Attach a cover image to a book.

        The upload is streamed: `read_chunk` never holds more than one chunk,
        and the declared facets are enforced as the bytes go past.
        """
        book = BOOKS.get(isbn)
        if book is None:
            return web.json_response(Error(code=404, message='no such book').model_dump(), status=404)
        size = 0
        while chunk := await cover.read_chunk():
            size += len(chunk)
        COVERS[isbn] = size
        return web.json_response(book.model_dump(), status=201)


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_view('/books', BooksView)
    app.router.add_view('/books/{isbn}', BookView)
    app.router.add_view('/books/{isbn}/cover', CoverView)
    # Last, and before the app starts: aiohttp freezes its router at startup.
    return add_raml_routes(
        app,
        title='Library',
        version='v2',
        description='Books, and what you may do with them.',
        documentation=[
            Documentation(
                title='Uploading a cover',
                content='Send `multipart/form-data` with one `cover` part, PNG, at most 1 MB.',
            )
        ],
    )


if __name__ == '__main__':
    web.run_app(build_app())
