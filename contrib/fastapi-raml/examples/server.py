"""A FastAPI server that serves its own RAML, with handlers that really run.

    pip install fastapi pydantic pyyaml uvicorn
    PYTHONPATH=. uvicorn fastapi_raml.examples.server:app --reload

Then:

| URL | What it is |
|-----|------------|
| `/books` | the API itself |
| `/raml` | the RAML source |
| `/raml.json` | the same document as `fastraml tree` output |
| `/raml-docs` | an HTML stub naming both |
| `/openapi.json`, `/docs` | FastAPI's own, untouched |

The last row is the point: OpenAPI and RAML are two renderings of one set of
pydantic models, not conversions of each other, and adding the second does not
disturb the first.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from fastapi_raml.serve import add_raml_routes


class Book(BaseModel):
    model_config = ConfigDict(extra='forbid')

    isbn: Annotated[str, Field(pattern=r'^\d{13}$', description='ISBN-13, digits only')]
    title: str
    pages: Annotated[int, Field(ge=1, le=10000)] = 100


class Error(BaseModel):
    code: int
    message: str


app = FastAPI(
    title='Library',
    version='v2',
    description='Books, and what you may do with them.',
)

BOOKS: dict[str, Book] = {
    '9780306406157': Book(isbn='9780306406157', title='The Hobbit', pages=310),
    '9781861972712': Book(isbn='9781861972712', title='Dune', pages=412),
}


@app.get('/books', summary='Every book')
def list_books(contains: Annotated[str | None, Query(description='Filter by title')] = None) -> list[Book]:
    if contains is None:
        return list(BOOKS.values())
    return [book for book in BOOKS.values() if contains.lower() in book.title.lower()]


@app.get(
    '/books/{isbn}',
    summary='One book',
    responses={404: {'model': Error, 'description': 'no such book'}},
)
def get_book(isbn: Annotated[str, Path(pattern=r'^\d{13}$')]) -> Book:
    book = BOOKS.get(isbn)
    if book is None:
        raise HTTPException(status_code=404, detail='no such book')
    return book


@app.post('/books', status_code=201, summary='Add a book')
def add_book(body: Book) -> Book:
    BOOKS[body.isbn] = body
    return body


# After the routes, so the first request renders every one of them. The cache
# keys on the router's version counter, so a route added later is picked up too.
add_raml_routes(app)
