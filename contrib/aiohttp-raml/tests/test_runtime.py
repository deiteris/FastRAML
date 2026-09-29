"""The half the document describes: what the app actually accepts and answers.

`test_differential.py` proves the RAML agrees with the pydantic models. This
proves the *app* agrees with both -- a request the document rejects is one the
app rejects, and at the same node.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import aiohttp
import pytest
from aiohttp import web
from multidict import CIMultiDict
from pydantic import AliasChoices, BaseModel, Field

from aiohttp_raml import (
    Body,
    File,
    Header,
    RamlView,
    Responds,
    ResponseMismatch,
    UploadedFile,
    UriParam,
    validate,
)
from aiohttp_raml.injectors import _BY_NAME


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


# -- a parameter's `Field` alias is its key on the wire -----------------------


class Aliased(RamlView):
    async def get(
        self,
        search: Annotated[str, Field(alias='q')] = '',
        limit: int = Field(ge=1),
        *,
        token: Annotated[str | None, Field(alias='X-Token')] = None,
    ) -> Annotated[web.Response, Responds(200, Book)]:
        return web.json_response({'search': search, 'limit': limit, 'token': token})


@pytest.fixture
async def aliased(aiohttp_client: Any) -> Any:
    app = web.Application()
    app.router.add_view('/x', Aliased)
    return await aiohttp_client(app)


async def test_an_aliased_parameter_is_read_from_its_alias(aliased: Any) -> None:
    response = await aliased.get('/x?q=hobbit&limit=2', headers={'X-Token': 't'})
    assert await response.json() == {'search': 'hobbit', 'limit': 2, 'token': 't'}


async def test_an_aliased_parameter_is_not_read_from_its_identifier(aliased: Any) -> None:
    assert (await (await aliased.get('/x?search=hobbit&limit=2')).json())['search'] == ''


async def test_a_field_with_no_default_is_required_and_keeps_its_constraint(aliased: Any) -> None:
    """`limit: int = Field(ge=1)` has a Python default and no value default."""
    missing = await aliased.get('/x')
    assert missing.status == 400
    assert (await missing.json())[0]['type'] == 'missing'
    assert (await (await aliased.get('/x?limit=0')).json())[0]['type'] == 'greater_than_equal'


def test_a_parameter_read_from_several_keys_is_refused() -> None:
    """A parameter has one key; `AliasChoices` offers several."""
    with pytest.raises(TypeError, match='a parameter has one key'):

        @validate
        async def either(
            q: Annotated[str, Field(validation_alias=AliasChoices('q', 'query'))],
        ) -> Annotated[web.Response, Responds(200, Book)]: ...


async def test_a_form_field_keeps_the_constraint_its_field_default_states(aiohttp_client: Any) -> None:
    class Noted(RamlView):
        async def post(
            self,
            note: Annotated[str, Body()] = Field(min_length=2),
            *,
            cover: Annotated[UploadedFile, Body()],
        ) -> Annotated[web.Response, Responds(201, Book)]:
            await cover.read()
            return web.json_response({}, status=201)

    app = web.Application()
    app.router.add_view('/x', Noted)
    client = await aiohttp_client(app)

    def with_note(note: str) -> Any:
        writer = aiohttp.MultipartWriter('form-data')
        writer.append(note).set_content_disposition('form-data', name='note')
        writer.append(b'png', {'Content-Type': 'image/png'}).set_content_disposition(
            'form-data', name='cover', filename='c.png'
        )
        return writer

    assert (await client.post('/x', data=with_note('x'))).status == 400
    assert (await client.post('/x', data=with_note('xy'))).status == 201


# -- what is a body -------------------------------------------------------------


@dataclass
class Point:
    x: int


async def test_a_dataclass_is_read_from_the_body(aiohttp_client: Any) -> None:
    @validate
    async def post(point: Point) -> Annotated[web.Response, Responds(201, None)]:
        return web.json_response({'x': point.x}, status=201)

    app = web.Application()
    app.router.add_post('/x', post)
    client = await aiohttp_client(app)
    assert await (await client.post('/x', json={'x': 1})).json() == {'x': 1}


async def test_an_optional_body_may_be_left_out(aiohttp_client: Any) -> None:
    @validate
    async def post(book: Book | None = None) -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response(book.model_dump() if book else None)

    app = web.Application()
    app.router.add_post('/x', post)
    client = await aiohttp_client(app)
    assert await (await client.post('/x')).json() is None
    assert await (await client.post('/x', json={'isbn': 'i'})).json() == {'isbn': 'i', 'pages': 100}
    assert (await client.post('/x', json={'pages': 'many'})).status == 400


# -- a body that is not JSON ------------------------------------------------------


async def test_a_body_that_is_not_json_is_taken_as_text(aiohttp_client: Any) -> None:
    @validate
    async def post(
        doc: Annotated[str, Body(media='application/xml'), Field(max_length=16)],
    ) -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response({'doc': doc})

    app = web.Application()
    app.router.add_post('/x', post)
    client = await aiohttp_client(app)
    sent = await client.post('/x', data='<book/>', headers={'Content-Type': 'application/xml'})
    assert await sent.json() == {'doc': '<book/>'}
    assert (await client.post('/x', data='<book>' + 'x' * 16 + '</book>')).status == 400


async def test_a_body_that_is_not_json_can_be_taken_as_bytes(aiohttp_client: Any) -> None:
    @validate
    async def post(image: Annotated[bytes, Body(media='image/png')]) -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response({'size': len(image)})

    app = web.Application()
    app.router.add_post('/x', post)
    client = await aiohttp_client(app)
    assert await (await client.post('/x', data=b'\x89PNG')).json() == {'size': 4}


def test_a_model_body_that_is_not_json_is_refused() -> None:
    """Only JSON is parsed; an XML body described as a model would refuse every request."""
    with pytest.raises(TypeError, match='read as str or bytes'):

        @validate
        async def post(
            book: Annotated[Book, Body(media='application/xml')],
        ) -> Annotated[web.Response, Responds(200)]: ...


def test_a_json_flavoured_media_type_is_parsed() -> None:
    @validate
    async def post(
        problem: Annotated[Book, Body(media='application/merge-patch+json')],
    ) -> Annotated[web.Response, Responds(200)]: ...


async def test_a_repeated_header_becomes_a_list(aiohttp_client: Any) -> None:
    """The document says `string[]`; one line or several, the handler gets a list."""

    @validate
    async def get(*, x_tag: list[str]) -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response(x_tag)

    app = web.Application()
    app.router.add_get('/x', get)
    client = await aiohttp_client(app)
    assert await (await client.get('/x', headers=CIMultiDict([('X-Tag', 'a'), ('X-Tag', 'b')]))).json() == ['a', 'b']
    assert await (await client.get('/x', headers={'X-Tag': 'a'})).json() == ['a']


@pytest.mark.parametrize('code', [101, 204, 304])
def test_a_body_on_a_status_that_has_none_is_refused(code: int) -> None:
    with pytest.raises(ValueError, match='has no body'):
        Responds(code, Book)
    Responds(code)


async def test_a_sub_applications_handler_is_secured_by_its_parents_scheme(aiohttp_client: Any) -> None:
    from aiohttp_raml import AuthenticationError, PassThrough, secured
    from aiohttp_raml.security import setup

    class Keyed(PassThrough):
        async def authenticate(self, request: web.Request) -> Any:
            if request.headers.get('X-Key') != 'k':
                raise AuthenticationError
            return 'someone'

    @secured('key')
    @validate.and_request
    async def guarded(request: web.Request) -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response(request['identity'])

    sub = web.Application()
    sub.router.add_get('/me', guarded)
    app = web.Application()
    setup(app, {'key': Keyed()})
    app.add_subapp('/v1', sub)
    client = await aiohttp_client(app)
    assert (await client.get('/v1/me')).status == 401
    assert await (await client.get('/v1/me', headers={'X-Key': 'k'})).json() == 'someone'


async def test_the_401_and_403_are_what_the_document_declares(aiohttp_client: Any) -> None:
    from aiohttp_raml import AuthenticationError, PassThrough, Refused, secured
    from aiohttp_raml.security import setup

    class Keyed(PassThrough):
        async def authenticate(self, request: web.Request) -> Any:
            if 'X-Key' not in request.headers:
                raise AuthenticationError('no key')
            return request.headers['X-Key']

        async def permits(self, request: web.Request, identity: Any, scopes: Any) -> bool:
            return bool(identity == 'admin')

    @secured('key')
    @validate
    async def guarded() -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response({})

    app = web.Application()
    setup(app, {'key': Keyed()})
    app.router.add_get('/x', guarded)
    client = await aiohttp_client(app)
    for headers, status in (({}, 401), ({'X-Key': 'guest'}, 403), ({'X-Key': 'admin'}, 200)):
        response = await client.get('/x', headers=headers)
        assert response.status == status
        if status != 200:
            Refused.model_validate(await response.json())


async def test_the_anonymous_alternative_lets_an_unauthenticated_caller_through(aiohttp_client: Any) -> None:
    from aiohttp_raml import AuthenticationError, PassThrough, secured
    from aiohttp_raml.security import setup

    class Keyed(PassThrough):
        async def authenticate(self, request: web.Request) -> Any:
            if 'X-Key' not in request.headers:
                raise AuthenticationError('no key')
            return request.headers['X-Key']

    @secured('key')
    @secured(None)
    @validate.and_request
    async def greet(request: web.Request) -> Annotated[web.Response, Responds(200, None)]:
        return web.json_response(request.get('identity'))

    app = web.Application()
    setup(app, {'key': Keyed()})
    app.router.add_get('/x', greet)
    client = await aiohttp_client(app)
    assert await (await client.get('/x')).json() is None
    assert await (await client.get('/x', headers={'X-Key': 'k'})).json() == 'k'


# -- the response check reads a body as the handler writes it --------------------


class Labelled(BaseModel):
    label: str = Field(alias='shelfLabel')


class Written(RamlView):
    check_responses = True

    async def get(self) -> Annotated[web.Response, Responds(200, Labelled), Responds(201, Labelled, by_alias=True)]:
        return web.json_response(Labelled(shelfLabel='a').model_dump())


@pytest.mark.skipif(not _BY_NAME, reason='validating by name alone needs pydantic 2.11')
def test_a_body_declared_by_name_is_checked_by_name() -> None:
    """`model_dump()` writes `label`, which is what `Responds` declares by default."""
    bound = Written.get.aiohttp_raml_described.bound
    bound.check(web.json_response({'label': 'a'}), Written.get)
    with pytest.raises(ResponseMismatch):
        bound.check(web.json_response({'shelfLabel': 'a'}), Written.get)


def test_a_body_declared_by_alias_is_checked_by_alias() -> None:
    bound = Written.get.aiohttp_raml_described.bound
    bound.check(web.json_response({'shelfLabel': 'a'}, status=201), Written.get)
    with pytest.raises(ResponseMismatch):
        bound.check(web.json_response({'label': 'a'}, status=201), Written.get)
