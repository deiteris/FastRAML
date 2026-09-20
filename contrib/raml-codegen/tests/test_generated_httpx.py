"""The generated client, imported and called.

The golden says what was generated and the gates say it is real code. Neither
says it *works*, and a client that type-checks and sends the wrong query string
is the failure this package would otherwise ship. So: generate, import, and put
a request through an `httpx.MockTransport`.
"""

from __future__ import annotations

import importlib
import sys

import httpx
import pytest

PAYLOAD = {
    'title': 'Dune',
    'isbn': '9780441013593',
    'price': {'amount': 9.99, 'currency': 'GBP'},
    'id': 'bk-1',
    'createdAt': '2024-01-01T00:00:00',
    'tags': ['scifi'],
}


@pytest.fixture(scope='module')
def client_package(generated, tmp_path_factory):
    """The generated package, importable."""
    destination = tmp_path_factory.mktemp('client')
    generated.write(destination)
    sys.path.insert(0, str(destination))
    try:
        yield importlib.import_module('bookstore_api')
    finally:
        sys.path.remove(str(destination))
        for name in [name for name in sys.modules if name.startswith('bookstore_api')]:
            del sys.modules[name]


def module(name: str):
    return importlib.import_module(name)


class TestModels:
    def test_a_body_round_trips(self, client_package):
        book = module('bookstore_api.models').Book.from_dict(PAYLOAD)
        assert book.title == 'Dune'
        assert book.price.currency == 'GBP'
        assert book.to_dict() == PAYLOAD

    def test_a_datetime_arrives_as_a_datetime(self, client_package):
        import datetime

        book = module('bookstore_api.models').Book.from_dict(PAYLOAD)
        assert book.created_at == datetime.datetime(2024, 1, 1)

    def test_an_absent_optional_is_unset_and_not_none(self, client_package):
        # `?tags=` and a key left off the body are different documents, which is
        # the whole reason `Unset` exists beside `None`.
        types = module('bookstore_api.types')
        without = {key: value for key, value in PAYLOAD.items() if key != 'tags'}
        book = module('bookstore_api.models').Book.from_dict(without)
        assert isinstance(book.tags, types.Unset)
        assert 'tags' not in book.to_dict()

    def test_a_recursive_type_terminates(self, client_package):
        chain = module('bookstore_api.models').Chain.from_dict({'next': {'next': {}}})
        assert chain.next_.next_.to_dict() == {}


class TestCalls:
    @staticmethod
    def dialled(client_package, handler):
        client = client_package.AuthenticatedClient(base_url='https://acme.books.example.com/v2', token='t0k')
        client.set_httpx_client(
            httpx.Client(
                base_url=client.base_url,
                headers=client._request_headers(),
                transport=httpx.MockTransport(handler),
            )
        )
        return client

    def test_query_parameters_reach_the_request(self, client_package):
        seen = {}

        def handler(request):
            seen['url'] = str(request.url)
            return httpx.Response(200, json=[PAYLOAD])

        client = self.dialled(client_package, handler)
        books = module('bookstore_api.api.books.get_books').sync(client=client, limit=5, offset=10)
        assert 'offset=10' in seen['url']
        assert 'limit=5' in seen['url']
        assert [one.title for one in books] == ['Dune']

    def test_an_unset_parameter_is_not_sent(self, client_package):
        seen = {}

        def handler(request):
            seen['url'] = str(request.url)
            return httpx.Response(200, json=[])

        client = self.dialled(client_package, handler)
        module('bookstore_api.api.books.get_books').sync(client=client, limit=5)
        assert 'offset' not in seen['url']

    def test_a_uri_parameter_is_substituted(self, client_package):
        seen = {}

        def handler(request):
            seen['path'] = request.url.path
            return httpx.Response(200, json=PAYLOAD)

        client = self.dialled(client_package, handler)
        module('bookstore_api.api.books.get_books_isbn').sync(client=client, isbn='9780441013593')
        assert seen['path'].endswith('/books/9780441013593')

    def test_an_authenticated_client_sends_its_token(self, client_package):
        seen = {}

        def handler(request):
            seen['auth'] = request.headers.get('authorization')
            return httpx.Response(200, json=[])

        client = self.dialled(client_package, handler)
        module('bookstore_api.api.books.get_books').sync(client=client)
        assert seen['auth'] == 'Bearer t0k'

    def test_the_detailed_variant_keeps_the_raw_response(self, client_package):
        client = self.dialled(client_package, lambda request: httpx.Response(200, json=[PAYLOAD]))
        result = module('bookstore_api.api.books.get_books').sync_detailed(client=client)
        assert result.status_code == 200
        assert result.content
        assert len(result.parsed) == 1

    def test_a_body_is_sent_as_the_declared_media_type(self, client_package):
        seen = {}

        def handler(request):
            seen['type'] = request.headers.get('content-type')
            seen['body'] = request.content
            return httpx.Response(201, json=PAYLOAD)

        client = self.dialled(client_package, handler)
        book = module('bookstore_api.models').Book.from_dict(PAYLOAD)
        module('bookstore_api.api.books.post_books').sync(client=client, body=book)
        assert seen['type'] == 'application/json'
        assert b'Dune' in seen['body']

    def test_an_undocumented_status_is_silent_by_default(self, client_package):
        client = self.dialled(client_package, lambda request: httpx.Response(418, json={}))
        assert module('bookstore_api.api.books.get_books').sync(client=client) is None

    def test_an_undocumented_status_raises_when_asked(self, client_package):
        client = self.dialled(client_package, lambda request: httpx.Response(418, json={}))
        client.raise_on_unexpected_status = True
        with pytest.raises(module('bookstore_api.errors').UnexpectedStatus):
            module('bookstore_api.api.books.get_books').sync(client=client)

    def test_a_documented_status_with_no_body_does_not_raise(self, client_package):
        # `DELETE /books/{isbn}` documents 204 and nothing else. Raising there
        # would make the documented answer an error.
        client = self.dialled(client_package, lambda request: httpx.Response(204))
        client.raise_on_unexpected_status = True
        assert module('bookstore_api.api.books.delete_books_isbn').sync(client=client, isbn='9780441013593') is None


class TestUnions:
    """The one field that does not know its own type until it has a value."""

    def test_a_union_body_is_serialised_rather_than_handed_over(self, client_package):
        # Identity conversion here hands `httpx` a dataclass, which is a crash
        # on a documented operation.
        seen = {}

        def handler(request):
            seen['body'] = request.content
            return httpx.Response(201, json=[])

        client = TestCalls.dialled(client_package, handler)
        book = module('bookstore_api.models').Book.from_dict(PAYLOAD)
        module('bookstore_api.api.shelves.post_shelves').sync(client=client, body=[book])
        assert b'Dune' in seen['body']

    def test_the_other_member_serialises_too(self, client_package):
        seen = {}

        def handler(request):
            seen['body'] = request.content
            return httpx.Response(201, json=[])

        client = TestCalls.dialled(client_package, handler)
        review = module('bookstore_api.models').Review.from_dict(
            {'rating': 5, 'author': {'name': 'A', 'verified': True}}
        )
        module('bookstore_api.api.shelves.post_shelves').sync(client=client, body=review)
        assert b'rating' in seen['body']


class TestSecurity:
    def test_a_scheme_says_where_its_credential_goes(self, client_package):
        security = module('bookstore_api.security')
        assert security.OAUTH2.header_name == 'Authorization'
        assert security.OAUTH2.prefix == 'Bearer'
        assert security.BASIC.prefix == 'Basic'
        assert 'read:books' in security.OAUTH2.scopes

    def test_a_scheme_builds_a_client_that_sends_it_that_way(self, client_package):
        seen = {}

        def handler(request):
            seen['auth'] = request.headers.get('authorization')
            return httpx.Response(200, json=[])

        security = module('bookstore_api.security')
        client = security.BASIC.client(base_url='https://acme.books.example.com/v2', token='dXNlcjpwdw==')
        client.set_httpx_client(
            httpx.Client(
                base_url=client.base_url,
                headers=client._request_headers(),
                transport=httpx.MockTransport(handler),
            )
        )
        module('bookstore_api.api.books.get_books').sync(client=client)
        assert seen['auth'] == 'Basic dXNlcjpwdw=='

    def test_an_operation_names_the_schemes_it_accepts(self, client_package):
        assert 'oauth2' in module('bookstore_api.api.books.post_books').SCHEMES

    def test_basic_credentials_have_a_helper(self, client_package):
        client_module = module('bookstore_api.client')
        assert client_module.basic_token('user', 'pw') == 'dXNlcjpwdw=='


class TestAsync:
    def test_the_async_entry_points_exist_and_are_coroutines(self, client_package):
        import inspect

        books = module('bookstore_api.api.books.get_books')
        assert inspect.iscoroutinefunction(books.asyncio)
        assert inspect.iscoroutinefunction(books.asyncio_detailed)
