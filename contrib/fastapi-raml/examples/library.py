"""An ordinary API: paths, parameters, responses, OAuth2 scopes.

Between them, this and `hard` are what the differential gate runs against. This
one exercises the endpoint half -- what `_method` and `_security` read off a
route -- and `hard` exercises the type half.
"""

from typing import Annotated

from fastapi import FastAPI, Header, Path, Query, Security
from fastapi.security import OAuth2AuthorizationCodeBearer
from pydantic import BaseModel, ConfigDict, Field

oauth = OAuth2AuthorizationCodeBearer(
    authorizationUrl='https://auth.example/authorize',
    tokenUrl='https://auth.example/token',
    scopes={'read:books': 'Read books', 'write:books': 'Add books'},
)


class Book(BaseModel):
    model_config = ConfigDict(extra='forbid')

    isbn: Annotated[str, Field(pattern=r'^\d{13}$', description='ISBN-13')]
    title: str
    pages: Annotated[int, Field(ge=1, le=10000)] = 100


class Error(BaseModel):
    code: int
    message: str


app = FastAPI(
    title='Library',
    version='v2',
    description='Books, and what you may do with them.',
    servers=[{'url': 'https://api.example/{ver}', 'variables': {'ver': {'default': 'v2'}}}],
)


@app.get(
    '/books/{isbn}',
    summary='One book',
    responses={404: {'model': Error, 'description': 'no such book'}},
)
def get_book(
    isbn: Annotated[str, Path(pattern=r'^\d{13}$')],
    fields: Annotated[list[str] | None, Query()] = None,
    trace: Annotated[str | None, Header()] = None,
    _: Annotated[str, Security(oauth, scopes=['read:books'])] = '',
) -> Book: ...


@app.post('/books', status_code=201)
def add_book(body: Book) -> Book: ...
