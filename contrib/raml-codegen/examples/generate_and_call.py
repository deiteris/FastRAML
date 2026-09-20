"""Generate a client from a tree document, then call it.

    uv run python examples/generate_and_call.py

The loop closes in one file: read the tree, generate, import, request. No parser
is involved — the tree was written by `fastraml tree` and committed, which is
the point of it being a wire format. The server is an `httpx.MockTransport`, so
nothing listens on a socket and the example needs no second process.
"""

from __future__ import annotations

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
    'tags': ['scifi', 'classic'],
}


def catalogue(request: httpx.Request) -> httpx.Response:
    """A bookstore with one book in it."""
    print(f'  <- {request.method} {request.url}')
    if request.url.path.endswith('/books'):
        return httpx.Response(200, json=[BOOK])
    return httpx.Response(404)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        destination = pathlib.Path(directory)
        document = json.loads(TREE.read_text(encoding='utf-8'))
        generated = generate(document, 'python', Settings())
        written = generated.write(destination)
        print(f'generated {len(written)} files for {generated.package}')

        sys.path.insert(0, str(destination))
        get_books = importlib.import_module('bookstore_api.api.books.get_books')
        models = importlib.import_module('bookstore_api.models')

        # The document says `oauth2` carries its credential in `Authorization`
        # after `Bearer`; `security.py` is where it says so.
        security = importlib.import_module('bookstore_api.security')
        client = security.OAUTH2.client(
            base_url='https://acme.books.example.com/v2',
            token='a-token',
        )
        client.set_httpx_client(
            httpx.Client(
                base_url=client.base_url,
                headers=client._request_headers(),
                transport=httpx.MockTransport(catalogue),
            )
        )

        books = get_books.sync(client=client, limit=10)
        for book in books:
            print(f'  {book.isbn}  {book.title}  {book.price.amount} {book.price.currency}')
            print(f'  created {book.created_at:%Y-%m-%d}, tags {", ".join(book.tags)}')

        print(f'\nand back again: {models.Book.from_dict(BOOK).to_dict() == BOOK}')


if __name__ == '__main__':
    main()
