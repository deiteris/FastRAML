"""Generate a server from a tree document, implement it, then call it.

    uv run python examples/implement_and_serve.py

The mirror of `generate_and_call.py`. That one reads the document and calls an
API; this one reads the same document and writes the API to be called. Together
they are the two directions `contrib/` exists to show: code-first is
`fastapi-raml`, and this is design-first.

No parser is involved -- the tree was written by `fastraml tree` and committed,
which is the point of it being a wire format. Nothing listens on a socket
either: `httpx.ASGITransport` calls the application in process.
"""

from __future__ import annotations

import asyncio
import datetime
import importlib
import json
import pathlib
import sys
import tempfile

import httpx

from raml_codegen import Settings, generate

TREE = pathlib.Path(__file__).resolve().parent.parent / 'tests' / 'api.json'

BOOK = {
    'title': 'Dune',
    'isbn': '9780441013593',
    'price': {'amount': 9.99, 'currency': 'GBP'},
    'id': 'bk-1',
    'createdAt': '2024-01-01T00:00:00',
    'tags': ['scifi'],
}


def build(destination: pathlib.Path):
    """Generate the server package and import it."""
    document = json.loads(TREE.read_text(encoding='utf-8'))
    generated = generate(document, 'python-fastapi', Settings(package='bookstore-server'))
    generated.write(destination)
    print(f'generated {len(generated.files)} files into {destination}')
    sys.path.insert(0, str(destination))
    return importlib.import_module('bookstore_server')


def implement(server):
    """Answer every operation the document describes.

    `abc` will not let this class be constructed while one is missing, which is
    what makes "implement it as described" something a machine checks.
    """
    models = importlib.import_module('bookstore_server.models')
    catalogue: dict[str, object] = {}

    class Bookstore(server.Api):
        async def post_books(self, *, body, credential):
            print(f'  post_books: {credential.scheme.name} token, scopes {credential.scopes}')
            catalogue[body.isbn] = body
            return body

        async def get_books(self, *, credential, offset=0, limit=20):
            print(f'  get_books: offset={offset} limit={limit} (both from the document)')
            return list(catalogue.values())[offset : offset + limit]

        async def get_books_isbn(self, *, isbn, credential):
            print(f'  get_books_isbn: credential={credential!r}')
            return catalogue.get(isbn) or models.Book(
                isbn=isbn,
                title='Unknown',
                price=models.Money(amount=0.0, currency='GBP'),
                id='bk-0',
                # `createdAt`, not `created_at`: a model accepts the name the
                # document gave the property and no other.
                createdAt=datetime.datetime(2024, 1, 1),
            )

        async def delete_books_isbn(self, *, isbn, credential):
            catalogue.pop(isbn, None)

        async def post_shelves(self, *, body, credential):
            return body

        async def get_deliveries(self, *, credential, since=None, status=None, city=None):
            return []

        async def get_publications(self, *, credential):
            return []

        async def get_search(self, *, credential, q=None):
            return []

    return server.create_app(Bookstore())


async def call(app) -> None:
    bearer = {'Authorization': 'Bearer a-token'}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://api.test') as client:
        print('\na body the document describes:')
        answer = await client.post('/books', json=BOOK, headers=bearer)
        print(f'  {answer.status_code} {answer.json()["title"]}')

        print('\na body it forbids -- the handler is never reached:')
        answer = await client.post('/books', json={**BOOK, 'isbn': 'not-an-isbn'}, headers=bearer)
        print(f'  {answer.status_code} {answer.json()["detail"][0]["msg"]}')

        print('\nno credential on a secured operation:')
        answer = await client.post('/books', json=BOOK)
        print(f'  {answer.status_code} WWW-Authenticate: {answer.headers["www-authenticate"]}')

        print('\nan operation the document says may be called either way:')
        answer = await client.get('/books/9780441013593')
        print(f'  {answer.status_code} with no credential at all')

        print('\nthe defaults the document states:')
        await client.get('/books', headers=bearer)

        print('\nand the OpenAPI the document produced:')
        schema = (await client.get('/openapi.json')).json()
        print(f'  {schema["info"]["title"]} {schema["info"]["version"]}: {len(schema["paths"])} paths')


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        server = build(pathlib.Path(directory))
        asyncio.run(call(implement(server)))


if __name__ == '__main__':
    main()
