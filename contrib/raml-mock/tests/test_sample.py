from __future__ import annotations

import pathlib

import aiohttp
import pytest
from fastraml import ParseOptions

from raml_mock import mock_server

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'fixtures'
SAMPLE = FIXTURES / 'sample' / 'api.raml'

BOOK = {
    'id': 'b-1',
    'createdAt': '2024-01-01T00:00:00Z',
    'title': 'Dune',
    'isbn': '9780441013593',
    'price': {'amount': 9.99, 'currency': 'USD'},
}
SHELF = [
    {
        'position': 1,
        'book': BOOK,
        'note': 'front of house',
        'locator': 'GB-SHELF-12.3-LEFT/reserve',
    }
]
DELIVERY = {
    'address': {
        'line1': '1 Bridge Street',
        'city': 'Bristol',
        'postcode': 'BS1 1AA',
        'country': 'GB',
    },
    'promisedFor': '2024-06-02T09:00:00Z',
}
PUBLICATION = {'kind': 'Publication', 'title': 'Dune'}


@pytest.fixture
async def sample_client():
    async with (
        mock_server(
            SAMPLE,
            options=ParseOptions(workspace_root=FIXTURES),
        ) as server,
        aiohttp.ClientSession(server.url) as client,
    ):
        yield client


class TestRepositorySample:
    async def test_collection_uses_the_book_item_example(self, sample_client):
        response = await sample_client.get('/books')
        assert response.status == 200
        assert await response.json() == [BOOK]

    async def test_post_composes_a_book_subtype_from_its_properties(self, sample_client):
        # `type: Book` is a subtype, which may narrow Book, so Book's own example
        # is never borrowed (docs/16 § 8.1). The properties' examples are used.
        submitted = {**BOOK, 'id': 'b-2', 'title': 'Neuromancer', 'isbn': '9780441569595'}
        response = await sample_client.post('/books', json=submitted)
        assert response.status == 201
        assert response.headers['Location'] == 'string'
        body = await response.json()
        assert body != BOOK
        assert (body['title'], body['isbn']) == (BOOK['title'], BOOK['isbn'])

    async def test_path_operation_composes_a_book_subtype_from_its_properties(self, sample_client):
        response = await sample_client.get('/books/9780441013593')
        assert response.status == 200
        body = await response.json()
        assert body != BOOK
        assert (body['title'], body['isbn']) == (BOOK['title'], BOOK['isbn'])

    async def test_empty_response_has_no_synthesized_body(self, sample_client):
        response = await sample_client.delete('/books/9780441013593')
        assert response.status == 204
        assert await response.read() == b''

    async def test_union_request_composes_the_shelf_subtype(self, sample_client):
        # The slot's `book: Book` is an alias of Book, so it carries Book's example.
        review = {'rating': 5, 'author': {'name': 'Ada', 'verified': True}}
        response = await sample_client.post('/shelves', json=review)
        assert response.status == 201
        body = await response.json()
        assert body != SHELF
        assert [slot['book'] for slot in body] == [BOOK]

    async def test_query_string_request_receives_the_delivery_item_example(self, sample_client):
        response = await sample_client.get('/deliveries?since=2024-06-01T00:00:00Z&city=Bristol')
        assert response.status == 200
        assert await response.json() == [DELIVERY]

    async def test_discriminated_item_example_is_used(self, sample_client):
        response = await sample_client.get('/publications')
        assert response.status == 200
        assert await response.json() == [PUBLICATION]

    async def test_union_query_request_receives_the_book_item_example(self, sample_client):
        response = await sample_client.get('/search?q=Dune&sort=-price')
        assert response.status == 200
        assert await response.json() == [BOOK]
