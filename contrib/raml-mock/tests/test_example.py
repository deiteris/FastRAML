from __future__ import annotations

import server
from aiohttp.test_utils import TestClient, TestServer


def test_example_exposes_an_entry_point() -> None:
    assert callable(server.main)


async def test_example_serves_the_shared_bookstore_definition() -> None:
    client = TestClient(TestServer(server.build()))
    await client.start_server()
    try:
        response = await client.get('/books')
        value = await response.json()
    finally:
        await client.close()
    assert response.status == 200
    assert [book['title'] for book in value] == ['Dune']
