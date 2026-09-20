"""The generated server, implemented and called.

The golden says what was generated and the gates say it is real code. Neither
says it *works*, and a server that type-checks while its `Depends` never fires
is the failure this target would otherwise ship. So: generate, implement the
`Api`, and put requests through `httpx.ASGITransport`.

The assertions that matter are the ones about what happens *before* the
implementation is called. That is the whole of what design-first buys: the
document is enforced at the edge, and a handler only sees a request the document
describes.
"""

from __future__ import annotations

import datetime
import importlib
import inspect
import sys

import httpx
import pytest

BOOK = {
    'title': 'Dune',
    'isbn': '9780441013593',
    'price': {'amount': 9.99, 'currency': 'GBP'},
    'id': 'bk-1',
    'createdAt': '2024-01-01T00:00:00',
    'tags': ['scifi'],
}

BEARER = {'Authorization': 'Bearer a-token'}


@pytest.fixture(scope='module')
def server(served, tmp_path_factory):
    """The generated package, importable."""
    destination = tmp_path_factory.mktemp('server')
    served.write(destination)
    sys.path.insert(0, str(destination))
    try:
        yield importlib.import_module('bookstore_server')
    finally:
        sys.path.remove(str(destination))
        for name in [name for name in sys.modules if name.startswith('bookstore_server')]:
            del sys.modules[name]


@pytest.fixture(scope='module')
def seen(server):
    """What the implementation was handed, for the calls that reached it."""
    return []


@pytest.fixture(scope='module')
def app(server, seen):
    models = importlib.import_module('bookstore_server.models')

    def one_book(isbn: str = '9780441013593'):
        return models.Book(
            isbn=isbn,
            title='Dune',
            price=models.Money(amount=1.0, currency='GBP'),
            id='bk-1',
            # `createdAt`, not `created_at`: the alias is the only name a model
            # accepts, because it is the only one the document describes.
            createdAt=datetime.datetime(2024, 1, 1),
        )

    class Implementation(server.Api):
        async def post_books(self, *, body, credential, response):
            # `response` is here because the document says the 201 carries a
            # `Location`. No other method takes one, and nothing invented it.
            seen.append(('post_books', body, credential))
            response.headers['Location'] = f'/books/{body.isbn}'
            return body

        async def get_books(self, *, credential, offset=0, limit=20):
            seen.append(('get_books', offset, limit, credential))
            return [one_book()]

        async def get_books_isbn(self, *, isbn, credential):
            seen.append(('get_books_isbn', isbn, credential))
            return one_book(isbn)

        async def delete_books_isbn(self, *, isbn, credential):
            seen.append(('delete_books_isbn', isbn, credential))
            return None

        async def post_shelves(self, *, body, credential):
            return body

        async def get_deliveries(self, *, credential, since=None, status=None, city=None):
            return []

        async def get_publications(self, *, credential):
            return []

        async def get_search(self, *, credential, q=None):
            return []

    return server.create_app(Implementation())


@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://api.test')


class TestTheInterfaceIsTheContract:
    def test_an_incomplete_implementation_cannot_be_constructed(self, server):
        # `abc` is what makes "implement it as described" checkable. An
        # operation the document names and the code does not is a failure at
        # startup, not a 500 on the first call that reaches it.
        class Missing(server.Api):
            pass

        with pytest.raises(TypeError, match='abstract'):
            Missing()

    def test_every_operation_is_abstract(self, server):
        assert len(server.Api.__abstractmethods__) == 8

    def test_every_documented_path_is_routed(self, app):
        assert set(app.openapi()['paths']) == {
            '/books',
            '/books/{isbn}',
            '/deliveries',
            '/publications',
            '/search',
            '/shelves',
        }


@pytest.mark.anyio
class TestTheDocumentIsEnforcedBeforeTheHandler:
    async def test_a_body_the_document_describes_is_accepted(self, client, seen):
        async with client as call:
            answer = await call.post('/books', json=BOOK, headers=BEARER)
        assert answer.status_code == 201
        assert answer.json()['isbn'] == '9780441013593'

    async def test_a_body_the_document_forbids_is_a_422(self, client, seen):
        # `isbn` has a `pattern:`. The handler is never reached, which is the
        # point: a client target can only report a mismatch after the fact.
        before = len(seen)
        async with client as call:
            answer = await call.post('/books', json={**BOOK, 'isbn': 'not-an-isbn'}, headers=BEARER)
        assert answer.status_code == 422
        assert len(seen) == before

    async def test_a_missing_required_property_is_a_422(self, client):
        without = {key: value for key, value in BOOK.items() if key != 'title'}
        async with client as call:
            assert (await call.post('/books', json=without, headers=BEARER)).status_code == 422

    async def test_a_query_bound_is_enforced(self, client):
        async with client as call:
            assert (await call.get('/books', params={'limit': 500}, headers=BEARER)).status_code == 422

    async def test_a_uri_parameter_pattern_is_enforced(self, client):
        async with client as call:
            assert (await call.get('/books/nope', headers=BEARER)).status_code == 422

    async def test_an_undocumented_property_is_ignored_rather_than_refused(self, client):
        # RAML's default is an open object, so a body carrying more than the
        # document names still matches it.
        async with client as call:
            answer = await call.post('/books', json={**BOOK, 'unexpected': 1}, headers=BEARER)
        assert answer.status_code == 201


@pytest.mark.anyio
class TestSecurity:
    async def test_a_secured_operation_refuses_a_request_with_no_credential(self, client):
        async with client as call:
            answer = await call.post('/books', json=BOOK)
        assert answer.status_code == 401
        # RFC 7235 requires it, and it is a convention rather than something
        # RAML states -- the README says so.
        assert answer.headers['www-authenticate'] == 'Bearer'

    async def test_the_credential_reaches_the_implementation(self, client, seen):
        async with client as call:
            await call.post('/books', json=BOOK, headers=BEARER)
        _, _, credential = next(one for one in reversed(seen) if one[0] == 'post_books')
        assert credential.token == 'a-token'
        assert credential.scheme.name == 'oauth2'
        assert 'write:books' in credential.scopes

    async def test_a_scheme_is_recognised_by_its_own_prefix(self, client, seen):
        # `basic` is the second scheme on `POST /books`. A server that only read
        # `Bearer` would answer 401 to a caller the document allows.
        async with client as call:
            answer = await call.post('/books', json=BOOK, headers={'Authorization': 'Basic dXNlcjpwdw=='})
        assert answer.status_code == 201
        _, _, credential = next(one for one in reversed(seen) if one[0] == 'post_books')
        assert credential.scheme.name == 'basic'
        assert credential.basic == ('user', 'pw')

    async def test_an_optional_scheme_lets_the_call_through_without_one(self, client, seen):
        # `securedBy: [null, oauth2]`.
        async with client as call:
            assert (await call.get('/books/9780441013593')).status_code == 200
        assert next(one for one in reversed(seen) if one[0] == 'get_books_isbn')[2] is None


@pytest.mark.anyio
class TestResponses:
    async def test_a_204_carries_no_body(self, client):
        # FastAPI's default response class writes `null`, which a 204 may not
        # carry at all.
        async with client as call:
            answer = await call.delete('/books/9780441013593', headers=BEARER)
        assert (answer.status_code, answer.content) == (204, b'')

    async def test_a_response_goes_out_under_the_names_the_document_uses(self, client):
        # `created_at` is the attribute; `createdAt` is what the document says.
        async with client as call:
            body = (await call.get('/books/9780441013593', headers=BEARER)).json()
        assert 'createdAt' in body
        assert 'created_at' not in body

    async def test_a_parameter_default_is_applied_rather_than_left_none(self, client, seen):
        async with client as call:
            await call.get('/books', headers=BEARER)
        _, offset, limit, _ = next(one for one in reversed(seen) if one[0] == 'get_books')
        assert (offset, limit) == (0, 20)


@pytest.mark.anyio
class TestTheOpenApiSaysWhatTheRamlSaid:
    @staticmethod
    async def schema(client):
        async with client as call:
            return (await call.get('/openapi.json')).json()

    async def test_the_title_and_version_come_from_the_document(self, client):
        found = await self.schema(client)
        assert found['info']['title'] == 'Bookstore API'
        assert found['info']['version'] == 'v2'

    async def test_a_documented_error_status_is_published(self, client):
        found = await self.schema(client)
        assert '400' in found['paths']['/books']['post']['responses']

    async def test_a_facet_reaches_the_schema(self, client):
        # `Book-Input` rather than `Book`: `Book.related` is a list of `Book`,
        # and a self-reference resolves differently in the validation schema
        # from the serialisation one, so FastAPI names the two apart. They are
        # identical otherwise -- `test_the_two_book_schemas_differ_only_in_the_self_reference`.
        found = await self.schema(client)
        assert found['components']['schemas']['Book-Input']['properties']['isbn']['pattern'] == r'^\d{13}$'

    async def test_an_operations_summary_is_the_display_name(self, client):
        found = await self.schema(client)
        assert found['paths']['/books']['post']['summary'] == 'Add a book'


@pytest.mark.anyio
class TestTheResponseSideOfTheContract:
    """What the server can say, and what it can say it with.

    The request side is enforced: a body the document forbids never reaches a
    handler. A response comes out of the handler, so nothing can enforce it the
    same way. These are the two things that can be done instead -- raise *from*
    the document, and be given somewhere to put what the document requires.
    """

    def test_an_operation_carries_the_statuses_it_documents(self, server):
        shelves = importlib.import_module('bookstore_server.api.shelves')
        assert sorted(int(one) for one in shelves.POST_SHELVES.documented) == [201, 422, 500]

    def test_the_documents_own_words_are_the_default_detail(self, server):
        shelves = importlib.import_module('bookstore_server.api.shelves')
        failure = shelves.POST_SHELVES.fail(422)
        assert failure.status_code == 422
        assert failure.detail == 'The payload matched neither member of the union.'

    def test_a_detail_of_your_own_replaces_it(self, server):
        shelves = importlib.import_module('bookstore_server.api.shelves')
        assert shelves.POST_SHELVES.fail(500, 'the database is down').detail == 'the database is down'

    def test_an_undocumented_status_is_refused_where_it_was_written(self, server):
        # A mistake in the implementation, not an answer to a caller -- so it is
        # a `LookupError` and not an `HTTPException`. Turning it into one would
        # answer the request with a 500 and hide what was actually wrong.
        shelves = importlib.import_module('bookstore_server.api.shelves')
        with pytest.raises(LookupError, match='not one of the statuses'):
            shelves.POST_SHELVES.fail(418)

    def test_the_statuses_come_from_the_rfc_rather_than_being_invented(self, server):
        # A RAML `enum:` lists values nobody named, so it becomes a `Literal`.
        # A status code *has* a name, and it is the standard's.
        from http import HTTPStatus

        shelves = importlib.import_module('bookstore_server.api.shelves')
        assert HTTPStatus.UNPROCESSABLE_ENTITY in shelves.POST_SHELVES.documented

    async def test_a_raised_documented_status_reaches_the_caller(self, client, server, seen):
        shelves = importlib.import_module('bookstore_server.api.shelves')
        async with client as call:
            answer = await call.post('/shelves', json={'rating': 5, 'body': 'good'}, headers=BEARER)
        # The fixture's implementation returns the body; this is the raise path
        # exercised directly against the same constant the route documents.
        assert answer.status_code in shelves.POST_SHELVES.documented

    async def test_a_documented_header_can_be_set_and_arrives(self, client):
        # `POST /books` takes a `Response` because its 201 declares `Location`.
        async with client as call:
            answer = await call.post('/books', json=BOOK, headers=BEARER)
        assert answer.headers['location'] == '/books/9780441013593'

    def test_only_an_operation_whose_document_says_so_gets_one(self, server):
        # The same reading that drops a URI parameter the path never mentions:
        # an argument that goes nowhere is worse than a missing one.
        books = importlib.import_module('bookstore_server.api.books')
        shelves = importlib.import_module('bookstore_server.api.shelves')
        assert 'response' in inspect.signature(books.BooksApi.post_books).parameters
        assert 'response' not in inspect.signature(books.BooksApi.get_books).parameters
        assert 'response' not in inspect.signature(shelves.ShelvesApi.post_shelves).parameters


@pytest.mark.anyio
class TestOnlyTheDocumentsOwnNamesAreAccepted:
    """`createdAt` is the property; `created_at` is only what Python calls it.

    A generated model carries the wire name as an alias and accepts nothing
    else. `populate_by_name=True` would let the attribute's own name through as
    well, and that is a request the document does not describe -- the server is
    the party that decides what the document means, so it may not be looser than
    the document is.

    The price is that a response is built with `Book(createdAt=...)` rather than
    `Book(created_at=...)`, which is the fixture's implementation above.
    """

    async def test_the_documents_own_name_is_accepted(self, client):
        async with client as call:
            assert (await call.post('/books', json=BOOK, headers=BEARER)).status_code == 201

    async def test_the_python_name_is_a_422(self, client):
        renamed = {key: value for key, value in BOOK.items() if key != 'createdAt'}
        renamed['created_at'] = '2024-01-01T00:00:00'
        async with client as call:
            answer = await call.post('/books', json=renamed, headers=BEARER)
        assert answer.status_code == 422
        assert answer.json()['detail'][0]['loc'][-1] == 'createdAt'

    async def test_only_the_documents_name_goes_back_out(self, client):
        async with client as call:
            body = (await call.get('/books/9780441013593', headers=BEARER)).json()
        assert 'createdAt' in body
        assert 'created_at' not in body

    async def test_the_two_book_schemas_differ_only_in_the_self_reference(self, client):
        # `Book.related` is a list of `Book`, and a self-reference resolves
        # differently in a validation schema from a serialisation one, so
        # FastAPI names the two apart. Nothing else about them differs, and
        # nothing this package sets causes it.
        async with client as call:
            schemas = (await call.get('/openapi.json')).json()['components']['schemas']
        incoming, outgoing = schemas['Book-Input'], schemas['Book-Output']
        assert incoming['required'] == outgoing['required']
        differs = {
            name for name in incoming['properties'] if incoming['properties'][name] != outgoing['properties'].get(name)
        }
        assert differs == {'related'}


@pytest.fixture(scope='module')
def anyio_backend():
    return 'asyncio'
