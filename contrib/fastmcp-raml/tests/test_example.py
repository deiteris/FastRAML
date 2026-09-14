"""The example, run.

It builds an MCP server over the sample document and lets `raml-mock` serve that
same document on a real socket, so calling a tool here goes the whole way: flat
arguments to an HTTP request built from RAML, over the wire, and back through the
output schema `views/jsonschema.py` derived from it.

That is the one path the rest of the suite takes apart. `test_routes.py` checks
what a route becomes and `test_flatten.py` checks the arguments; nothing else
sends a request to something that is not a mock transport.

Importing the example is also what keeps it working. An example nobody runs is
documentation that has stopped being true.
"""

from __future__ import annotations

import bookstore
import pytest
from fastmcp import Client


@pytest.fixture
def server():
    return bookstore.build()


class TestItServesTheDocument:
    async def test_every_operation_is_a_tool(self, server):
        async with Client(server) as connected:
            names = {tool.name for tool in await connected.list_tools()}
        assert names == {
            'get_books',
            'post_books',
            'get_books_isbn',
            'delete_books_isbn',
            'get_deliveries',
            'get_publications',
            'get_search',
            'post_shelves',
        }

    async def test_the_documentation_is_readable(self, server):
        async with Client(server) as connected:
            contents = await connected.read_resource('docs://pagination')
        assert 'offset' in contents[0].text


class TestACallReachesTheBackend:
    async def test_a_path_parameter_addresses_one_book(self, server):
        async with Client(server) as connected:
            result = await connected.call_tool('get_books_isbn', {'isbn': '9780441013593'})
        assert result.structured_content['title'] == 'Dune'

    async def test_a_query_string_becomes_a_query(self, server):
        # `/deliveries` declares `queryString: DeliveryQuery`, so these four
        # arguments exist only because that type's properties were expanded.
        async with Client(server) as connected:
            result = await connected.call_tool('get_deliveries', {'since': '2024-06-01T00:00:00Z', 'city': 'Bristol'})
        assert result.structured_content['result'][0]['address']['city'] == 'Bristol'

    async def test_a_body_is_rebuilt_from_flat_arguments(self, server):
        async with Client(server) as connected:
            result = await connected.call_tool(
                'post_books',
                {
                    'id': 'b-9',
                    'createdAt': '2024-03-01T00:00:00Z',
                    'title': 'Neuromancer',
                    'isbn': '9780441569595',
                    'price': {'amount': 7.5, 'currency': 'USD'},
                },
            )
        # The response comes from `Book.example`, not from a Python handler that
        # echoes the request body.
        assert result.structured_content['title'] == 'Dune'

    async def test_a_response_with_no_body_is_not_an_error(self, server):
        # `delete` declares `204:` and nothing under it.
        async with Client(server) as connected:
            result = await connected.call_tool('delete_books_isbn', {'isbn': '9780441013593'})
        assert result.structured_content is None


class TestTheRamlSuppliesResponses:
    async def test_a_type_example_passes_the_output_schema(self, server):
        async with Client(server) as connected:
            result = await connected.call_tool('get_books_isbn', {'isbn': '9780441013593'})
        assert result.structured_content['price']['currency'] == 'USD'

    async def test_a_collection_uses_its_item_type_example(self, server):
        async with Client(server) as connected:
            result = await connected.call_tool('get_books', {})
        assert [book['title'] for book in result.structured_content['result']] == ['Dune']


def test_describe_names_what_was_dropped(server, capsys):
    bookstore.describe(server)
    printed = capsys.readouterr().out
    assert 'get_books' in printed
    assert 'docs://pagination' in printed
    assert 'securedBy' in printed
