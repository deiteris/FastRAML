"""The half the document describes: what the app actually accepts and answers.

`test_differential.py` proves the RAML agrees with the pydantic models. This
proves the *app* agrees with both -- a request the document rejects is one the
app rejects, and at the same node.
"""

from __future__ import annotations

from typing import Annotated, Any

import aiohttp
import pytest
from aiohttp import web
from pydantic import BaseModel, Field

from aiohttp_raml import (
    File,
    Header,
    RamlView,
    Responds,
    ResponseMismatch,
    UploadedFile,
    UriParam,
    validate,
)


class Book(BaseModel):
    isbn: str
    pages: int = 100


class Error(BaseModel):
    code: int


class BooksView(RamlView):
    async def get(
        self,
        title: str | None = None,
        tags: list[str] | None = None,
        *,
        request_id: Annotated[str | None, Header('X-Request-Id')] = None,
    ) -> Annotated[web.Response, Responds(200, list[Book])]:
        return web.json_response({'title': title, 'tags': tags, 'request_id': request_id})

    async def post(self, body: Book) -> Annotated[web.Response, Responds(201, Book)]:
        return web.json_response(body.model_dump(), status=201)


class BookView(RamlView):
    async def get(
        self, isbn: Annotated[str, Field(pattern=r'^\d{13}$')], /
    ) -> Annotated[web.Response, Responds(200, Book), Responds(404, Error)]:
        return web.json_response({'isbn': isbn})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_view('/books', BooksView)
    app.router.add_view('/books/{isbn}', BookView)
    return app


@pytest.fixture
async def client(aiohttp_client: Any) -> Any:
    return await aiohttp_client(build_app())


# -- the request is validated where the document says it is --------------------


async def test_a_query_parameter_reaches_the_handler(client: Any) -> None:
    assert (await (await client.get('/books?title=hobbit')).json())['title'] == 'hobbit'


async def test_a_repeated_query_parameter_becomes_a_list(client: Any) -> None:
    """`?tags=a&tags=b` is one parameter with two values, and the annotation says so."""
    assert (await (await client.get('/books?tags=a&tags=b')).json())['tags'] == ['a', 'b']


async def test_a_single_value_for_a_sequence_parameter_is_still_a_list(client: Any) -> None:
    assert (await (await client.get('/books?tags=a')).json())['tags'] == ['a']


async def test_a_header_is_matched_by_its_wire_name(client: Any) -> None:
    response = await client.get('/books', headers={'X-Request-Id': 'abc'})
    assert (await response.json())['request_id'] == 'abc'


async def test_a_header_match_is_case_insensitive(client: Any) -> None:
    response = await client.get('/books', headers={'x-request-id': 'abc'})
    assert (await response.json())['request_id'] == 'abc'


async def test_a_bad_query_parameter_is_a_400_naming_the_node(client: Any) -> None:
    response = await client.get('/books?tags=a&title=x&tags=b')
    assert response.status == 200  # sanity: that request is fine
    response = await client.post('/books', json={'isbn': 'x', 'pages': 'many'})
    assert response.status == 400
    assert (await response.json())[0]['in'] == 'body'


async def test_a_uri_parameter_is_validated_against_its_constraint(client: Any) -> None:
    """The `pattern` in the document is the one the route enforces."""
    assert (await client.get('/books/9780306406157')).status == 200
    response = await client.get('/books/nope')
    assert response.status == 400
    assert (await response.json())[0]['in'] == 'uriParameters'


async def test_malformed_json_is_a_400_rather_than_a_crash(client: Any) -> None:
    """Every 400 is a `RequestError[]`, including this one."""
    response = await client.post('/books', data='{not json', headers={'Content-Type': 'application/json'})
    assert response.status == 400
    failures = await response.json()
    assert [failure['in'] for failure in failures] == ['body']
    assert failures[0]['type'] == 'json_invalid'


async def test_a_body_reaches_the_handler_as_the_model(client: Any) -> None:
    response = await client.post('/books', json={'isbn': '9' * 13})
    assert response.status == 201
    assert (await response.json()) == {'isbn': '9' * 13, 'pages': 100}


async def test_a_verb_the_view_does_not_define_is_405(client: Any) -> None:
    assert (await client.delete('/books')).status == 405


# -- responses are checked only when asked ------------------------------------


class Liar(RamlView):
    check_responses = True

    async def get(self) -> Annotated[web.Response, Responds(200, Book)]:
        return web.json_response({'not': 'a book'})

    async def post(self) -> Annotated[web.Response, Responds(201, Book)]:
        return web.json_response({'isbn': 'x'}, status=418)


class QuietLiar(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(200, Book)]:
        return web.json_response({'not': 'a book'})


def test_a_body_that_does_not_match_what_was_declared_is_caught() -> None:
    """Nothing static can catch this: `web.json_response` takes `Any`."""
    bound = Liar.get.aiohttp_raml_described.bound
    with pytest.raises(ResponseMismatch, match='200 body it does not declare'):
        bound.check(web.json_response({'not': 'a book'}), Liar.get)


def test_an_undeclared_status_code_is_caught() -> None:
    bound = Liar.post.aiohttp_raml_described.bound
    with pytest.raises(ResponseMismatch, match='returned 418'):
        bound.check(web.json_response({'isbn': 'x'}, status=418), Liar.post)


def test_a_matching_response_passes() -> None:
    bound = Liar.get.aiohttp_raml_described.bound
    bound.check(web.json_response({'isbn': 'x', 'pages': 1}), Liar.get)


async def test_a_mismatch_fails_the_request_rather_than_passing_quietly(aiohttp_client: Any) -> None:
    """It raises out of the handler, so the caller gets a 500 and the log names it."""
    app = web.Application()
    app.router.add_view('/x', Liar)
    client = await aiohttp_client(app)
    assert (await client.get('/x')).status == 500


async def test_the_check_is_off_by_default(aiohttp_client: Any) -> None:
    """It costs a JSON parse per response, so it is opt-in."""
    app = web.Application()
    app.router.add_view('/x', QuietLiar)
    client = await aiohttp_client(app)
    assert (await client.get('/x')).status == 200


# -- the function forms --------------------------------------------------------


async def test_a_plain_decorated_handler_is_validated(aiohttp_client: Any) -> None:
    @validate
    async def listing(title: str | None = None) -> Annotated[web.Response, Responds(200, list[Book])]:
        return web.json_response({'title': title})

    app = web.Application()
    app.router.add_get('/books', listing)
    client = await aiohttp_client(app)
    assert (await (await client.get('/books?title=x')).json())['title'] == 'x'


async def test_and_request_passes_the_request_through(aiohttp_client: Any) -> None:
    @validate.and_request
    async def listing(
        request: web.Request, title: str | None = None
    ) -> Annotated[web.Response, Responds(200, list[Book])]:
        return web.json_response({'path': request.path, 'title': title})

    app = web.Application()
    app.router.add_get('/books', listing)
    client = await aiohttp_client(app)
    assert (await (await client.get('/books?title=x')).json()) == {'path': '/books', 'title': 'x'}


async def test_a_uri_marker_overrides_the_position(aiohttp_client: Any) -> None:
    @validate.and_request
    async def one(
        request: web.Request, isbn: Annotated[str, UriParam()] = ''
    ) -> Annotated[web.Response, Responds(200, Book)]:
        return web.json_response({'isbn': isbn})

    app = web.Application()
    app.router.add_get('/books/{isbn}', one)
    client = await aiohttp_client(app)
    assert (await (await client.get('/books/123')).json()) == {'isbn': '123'}


# -- what the declarations refuse outright ------------------------------------


def test_two_bodies_are_refused_at_decoration_time() -> None:
    with pytest.raises(TypeError, match='a request has one'):

        @validate
        async def two(a: Book, b: Error) -> Annotated[web.Response, Responds(200, Book)]: ...


def test_an_unannotated_parameter_is_refused() -> None:
    with pytest.raises(TypeError, match='has no annotation'):

        @validate
        async def odd(a) -> Annotated[web.Response, Responds(200, Book)]: ...


def test_a_status_code_declared_twice_is_refused() -> None:
    with pytest.raises(TypeError, match='declared twice'):

        @validate
        async def twice() -> Annotated[web.Response, Responds(200, Book), Responds(200, Error)]: ...


def test_a_status_code_outside_the_range_is_refused() -> None:
    with pytest.raises(ValueError, match='not an HTTP status code'):
        Responds(42, Book)


class Strict(RamlView):
    check_responses = True

    async def post(self, body: Book) -> Annotated[web.Response, Responds(201, Book)]:
        return web.json_response(body.model_dump(), status=201)


async def test_the_response_check_does_not_fire_on_a_validation_failure(aiohttp_client: Any) -> None:
    """The 400 is this package's, not the handler's; a handler need not declare it."""
    app = web.Application()
    app.router.add_view('/x', Strict)
    client = await aiohttp_client(app)
    assert (await client.post('/x', json={'pages': 'many'})).status == 400
    assert (await client.post('/x', json={'isbn': 'x'})).status == 201


# -- multipart -----------------------------------------------------------------


class Meta(BaseModel):
    title: str


class UploadView(RamlView):
    async def post(
        self,
        meta: Meta,
        cover: Annotated[UploadedFile, File(file_types=['image/png'], max_size=32)],
    ) -> Annotated[web.Response, Responds(201, Book)]:
        data = await cover.read()
        return web.json_response(
            {
                'title': meta.title,
                'filename': cover.filename,
                'content_type': cover.content_type,
                'size': cover.size,
                'body': data.decode(),
            },
            status=201,
        )


def form(*, meta: str = '{"title": "x"}', cover: bytes = b'png', content_type: str = 'image/png') -> Any:
    writer = aiohttp.MultipartWriter('form-data')
    part = writer.append(meta, {'Content-Type': 'application/json'})
    part.set_content_disposition('form-data', name='meta')
    part = writer.append(cover, {'Content-Type': content_type})
    part.set_content_disposition('form-data', name='cover', filename='cover.png')
    return writer


@pytest.fixture
async def uploads(aiohttp_client: Any) -> Any:
    app = web.Application()
    app.router.add_view('/upload', UploadView)
    return await aiohttp_client(app)


async def test_a_field_and_a_file_both_reach_the_handler(uploads: Any) -> None:
    response = await uploads.post('/upload', data=form())
    assert response.status == 201
    assert await response.json() == {
        'title': 'x',
        'filename': 'cover.png',
        'content_type': 'image/png',
        'size': 3,
        'body': 'png',
    }


async def test_the_file_is_not_read_until_the_handler_asks(uploads: Any) -> None:
    """`size` is 0 on arrival; the bytes come off the wire on `read`."""
    seen = {}

    class Lazy(RamlView):
        async def post(self, cover: UploadedFile) -> Annotated[web.Response, Responds(201, Book)]:
            seen['before'] = cover.size
            await cover.read()
            seen['after'] = cover.size
            return web.json_response({}, status=201)

    app = web.Application()
    app.router.add_view('/lazy', Lazy)
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        writer = aiohttp.MultipartWriter('form-data')
        part = writer.append(b'0123456789', {'Content-Type': 'image/png'})
        part.set_content_disposition('form-data', name='cover', filename='c.png')
        assert (await client.post('/lazy', data=writer)).status == 201
    assert seen == {'before': 0, 'after': 10}


async def test_a_file_type_not_declared_is_refused(uploads: Any) -> None:
    response = await uploads.post('/upload', data=form(content_type='image/gif'))
    assert response.status == 400
    failures = await response.json()
    assert failures[0]['loc'] == ['cover']
    assert 'is not one of' in failures[0]['msg']


async def test_a_file_over_the_declared_size_is_refused(uploads: Any) -> None:
    response = await uploads.post('/upload', data=form(cover=b'x' * 64))
    assert response.status == 400
    assert 'over the 32 bytes allowed' in (await response.json())[0]['msg']


async def test_a_non_multipart_body_is_refused(uploads: Any) -> None:
    response = await uploads.post('/upload', json={'meta': {'title': 'x'}})
    assert response.status == 400
    assert (await response.json())[0]['type'] == 'multipart_required'


async def test_a_field_part_is_validated_like_any_other_body(uploads: Any) -> None:
    response = await uploads.post('/upload', data=form(meta='{"title": 42}'))
    assert response.status == 400
    assert (await response.json())[0]['in'] == 'body'


def test_a_field_declared_after_a_file_is_refused_at_decoration_time() -> None:
    """Parts arrive in declaration order, so a field after a file cannot be read."""
    with pytest.raises(TypeError, match='declared after a file'):

        @validate
        async def wrong(cover: UploadedFile, meta: Meta) -> Annotated[web.Response, Responds(201, Book)]: ...


# -- the 400 is declared --------------------------------------------------------


async def test_the_declared_400_matches_what_the_app_returns(aiohttp_client: Any) -> None:
    """The document says `RequestError[]`; the response check proves it is one."""

    class Checked(RamlView):
        check_responses = True

        async def get(self, n: int = 0) -> Annotated[web.Response, Responds(200, Book)]:
            return web.json_response({'isbn': 'x', 'pages': n})

    app = web.Application()
    app.router.add_view('/x', Checked)
    client = await aiohttp_client(app)
    assert (await client.get('/x?n=nope')).status == 400
    assert (await client.get('/x?n=1')).status == 200


class WireNamed(RamlView):
    async def get(
        self,
        *,
        request_id: Annotated[str, Header('X-Request-Id')],
    ) -> Annotated[web.Response, Responds(200, Book)]:
        return web.json_response({'seen': request_id})


async def test_a_header_whose_wire_name_is_no_identifier_still_validates(aiohttp_client: Any) -> None:
    """The model is keyed by the parameter name; `X-Request-Id` is not one."""
    app = web.Application()
    app.router.add_view('/x', WireNamed)
    client = await aiohttp_client(app)
    assert (await (await client.get('/x', headers={'X-Request-Id': 'abc'})).json()) == {'seen': 'abc'}
    missing = await client.get('/x')
    assert missing.status == 400
    assert (await missing.json())[0]['loc'] == ['request_id']


async def test_an_undeclared_query_parameter_is_ignored(client: Any) -> None:
    assert (await client.get('/books?title=x&unknown=1')).status == 200


async def test_a_part_can_be_inspected_before_it_is_read(aiohttp_client: Any) -> None:
    """`open` advances to the part and checks `file_types`, reading no bytes."""
    seen = {}

    class Peek(RamlView):
        async def post(self, cover: UploadedFile) -> Annotated[web.Response, Responds(201, Book)]:
            seen['before'] = (cover.opened, cover.filename)
            await cover.open()
            seen['after'] = (cover.opened, cover.filename, cover.content_type, cover.size)
            return web.json_response({}, status=201)

    app = web.Application()
    app.router.add_view('/peek', Peek)
    client = await aiohttp_client(app)
    writer = aiohttp.MultipartWriter('form-data')
    part = writer.append(b'abc', {'Content-Type': 'image/png'})
    part.set_content_disposition('form-data', name='cover', filename='c.png')
    assert (await client.post('/peek', data=writer)).status == 201
    assert seen['before'] == (False, None)
    assert seen['after'] == (True, 'c.png', 'image/png', 0)
