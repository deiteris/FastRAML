"""What `render` puts in the document, rule by rule.

`test_differential.py` is the gate for the type half -- it compares verdicts,
which is stronger than any assertion about output. The endpoint half has no
second implementation to disagree with, so each rule here names itself.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from aiohttp import web
from pydantic import BaseModel, Field

from aiohttp_raml import (
    AuthenticationError,
    BasicAuth,
    Body,
    CustomScheme,
    DigestAuth,
    File,
    Header,
    OAuth1,
    OAuth2,
    PassThrough,
    RamlView,
    Responds,
    UploadedFile,
    UriParam,
    exclude,
    render,
    secured,
    validate,
)
from aiohttp_raml.params import wire_name
from aiohttp_raml.security import setup as setup_security


class Book(BaseModel):
    isbn: str


class Error(BaseModel):
    code: int


def rendered(app: web.Application, **metadata: Any) -> tuple[dict[str, Any], list[str]]:
    report = render(app, title='T', **metadata)
    return report.document.render(), report.dropped


def one_view(path: str, view: type) -> web.Application:
    app = web.Application()
    app.router.add_view(path, view)
    return app


# -- the four places a parameter can be ---------------------------------------


class ParamsView(RamlView):
    async def get(
        self,
        isbn: str,
        /,
        title: str | None = None,
        pages: Annotated[int, Field(ge=1)] = 10,
        *,
        x_request_id: str | None = None,
    ) -> Annotated[web.Response, Responds(200, Book)]: ...

    async def post(self, body: Book) -> Annotated[web.Response, Responds(201, Book)]: ...


def test_each_parameter_lands_in_the_raml_node_its_position_names() -> None:
    document, dropped = rendered(one_view('/books/{isbn}', ParamsView))
    resource = document['/books']['/{isbn}']
    assert list(resource['uriParameters']) == ['isbn']
    assert sorted(resource['get']['queryParameters']) == ['pages', 'title']
    assert list(resource['get']['headers']) == ['X-Request-Id']
    assert resource['post']['body'] == {'application/json': 'Book'}
    assert dropped == []


def test_a_constraint_beside_a_parameter_survives() -> None:
    document, _ = rendered(one_view('/books/{isbn}', ParamsView))
    assert document['/books']['/{isbn}']['get']['queryParameters']['pages']['minimum'] == 1


def test_an_optional_query_parameter_is_not_required() -> None:
    """RAML's default in the position is `required: true`, so silence means mandatory."""
    document, _ = rendered(one_view('/books/{isbn}', ParamsView))
    pages = document['/books']['/{isbn}']['get']['queryParameters']['pages']
    assert pages['required'] is False
    assert pages['default'] == 10


class Defaulted(RamlView):
    async def get(self, isbn: str = 'x', /) -> Annotated[web.Response, Responds(200, Book)]: ...


def test_a_uri_parameter_is_required_whatever_the_signature_says() -> None:
    """It is part of the path, so `required: false` on one contradicts the path."""
    document, _ = rendered(one_view('/books/{isbn}', Defaulted))
    assert document['/books']['/{isbn}']['uriParameters'] == {'isbn': {'type': 'string', 'default': 'x'}}


@pytest.mark.parametrize(
    ('identifier', 'wire'),
    [('x_request_id', 'X-Request-Id'), ('authorization', 'Authorization'), ('x_api_key', 'X-Api-Key')],
)
def test_a_header_is_named_as_it_travels(identifier: str, wire: str) -> None:
    assert wire_name(identifier) == wire


class Marked(RamlView):
    async def get(
        self, *, request_id: Annotated[str | None, Header('X-Request-Id')] = None
    ) -> Annotated[web.Response, Responds(204)]: ...


def test_a_marker_names_the_header_rather_than_guessing_it() -> None:
    document, dropped = rendered(one_view('/x', Marked))
    assert list(document['/x']['get']['headers']) == ['X-Request-Id']
    assert dropped == []


def test_the_marker_does_not_reach_the_type_walk() -> None:
    """`Header(...)` is metadata about the position, not a facet of the value."""
    document, dropped = rendered(one_view('/x', Marked))
    assert document['/x']['get']['headers']['X-Request-Id']['type'] == 'string | nil'
    assert dropped == []


# -- paths ---------------------------------------------------------------------


class Root(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(200, Book)]: ...


def test_the_base_uri_itself_is_a_resource_and_not_the_document_root() -> None:
    """`get:` beside `title:` is not RAML; an API answering on `/` writes `/:`."""
    document, _ = rendered(one_view('/', Root))
    assert 'get' not in document
    assert document['/']['get']['responses'] == {'200': {'body': {'application/json': 'Book'}}}


class Deep(RamlView):
    async def get(self, isbn: str, /) -> Annotated[web.Response, Responds(204)]: ...


def test_a_path_nests_the_way_raml_nests_it() -> None:
    document, _ = rendered(one_view('/a/b/{isbn}', Deep))
    assert 'get' in document['/a']['/b']['/{isbn}']


# -- verbs and responses --------------------------------------------------------


class Two(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...
    async def delete(self) -> Annotated[web.Response, Responds(204)]: ...


def test_a_view_describes_the_verbs_it_defines() -> None:
    """`add_view` registers the method `*`, so the verbs come off the class."""
    document, _ = rendered(one_view('/x', Two))
    assert sorted(key for key in document['/x'] if not key.startswith('/')) == ['delete', 'get']


class Both(RamlView):
    async def get(
        self,
    ) -> Annotated[
        web.Response,
        Responds(200, Book, 'the book'),
        Responds(404, Error, 'no such book'),
    ]:
        """One book."""


def test_each_declaration_becomes_one_response_with_its_description() -> None:
    document, dropped = rendered(one_view('/x', Both))
    assert document['/x']['get']['description'] == 'One book.'
    assert document['/x']['get']['responses'] == {
        '200': {'description': 'the book', 'body': {'application/json': 'Book'}},
        '404': {'description': 'no such book', 'body': {'application/json': 'Error'}},
    }
    assert dropped == []


class Empty(RamlView):
    async def delete(self) -> Annotated[web.Response, Responds(204)]: ...


def test_a_status_code_with_no_payload_has_no_body() -> None:
    document, _ = rendered(one_view('/x', Empty))
    assert document['/x']['delete']['responses'] == {'204': {}}


class Silent(RamlView):
    async def get(self) -> web.Response: ...


def test_a_handler_declaring_no_response_gets_no_responses_node() -> None:
    """Writing a 200 would be the renderer deciding what the handler did not say."""
    document, dropped = rendered(one_view('/x', Silent))
    assert 'responses' not in document['/x']['get']
    assert dropped == []


class Xml(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(200, Book, media='application/xml')]: ...


def test_a_response_can_name_its_media_type() -> None:
    document, _ = rendered(one_view('/x', Xml))
    assert document['/x']['get']['responses']['200']['body'] == {'application/xml': 'Book'}


# -- security -------------------------------------------------------------------


class Basic(BasicAuth):
    """A password."""

    async def authenticate(self, request: web.Request) -> str:
        return ''


class Digest(DigestAuth):
    """A nonce."""

    async def authenticate(self, request: web.Request) -> str:
        return ''


class Bearer(PassThrough):
    """A JWT."""

    async def authenticate(self, request: web.Request) -> str:
        return ''


class Weird(CustomScheme):
    """Something the spec does not name."""

    raml_type = 'x-custom'

    async def authenticate(self, request: web.Request) -> str:
        return ''


class Books(OAuth2):
    """Scopes."""

    async def authenticate(self, request: web.Request) -> str:
        raise AuthenticationError('no')


class Legacy(OAuth1):
    """Three legs."""

    async def authenticate(self, request: web.Request) -> str:
        return ''


def secured_app(schemes: dict[str, Any], view: type) -> web.Application:
    app = web.Application()
    setup_security(app, schemes)
    app.router.add_view('/x', view)
    return app


class Open(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


@pytest.mark.parametrize(
    ('scheme', 'raml_type'),
    [(Basic(), 'Basic Authentication'), (Digest(), 'Digest Authentication'), (Weird(), 'x-custom')],
)
def test_a_scheme_declares_itself_as_its_raml_type(scheme: Any, raml_type: str) -> None:
    document, dropped = rendered(secured_app({'s': scheme}, Open))
    assert document['securitySchemes']['s']['type'] == raml_type
    assert dropped == []


def test_a_pass_through_says_which_header_carries_the_credential() -> None:
    scheme = Bearer(headers={'Authorization': {'type': 'string'}})
    document, dropped = rendered(secured_app({'jwt': scheme}, Open))
    assert document['securitySchemes']['jwt'] == {
        'type': 'Pass Through',
        'description': 'A JWT.',
        'describedBy': {'headers': {'Authorization': {'type': 'string'}}},
    }
    assert dropped == []


def test_a_pass_through_can_carry_a_query_parameter_instead() -> None:
    scheme = Bearer(query_parameters={'api_key': {'type': 'string'}})
    document, _ = rendered(secured_app({'k': scheme}, Open))
    assert document['securitySchemes']['k']['describedBy'] == {'queryParameters': {'api_key': {'type': 'string'}}}


def test_oauth2_settings_are_ramls_own() -> None:
    scheme = Books(
        access_token_uri='https://auth.example/token',
        authorization_uri='https://auth.example/authorize',
        grants=['authorization_code'],
        scopes=['read', 'write'],
    )
    document, dropped = rendered(secured_app({'oauth': scheme}, Open))
    assert document['securitySchemes']['oauth']['settings'] == {
        'accessTokenUri': 'https://auth.example/token',
        'authorizationUri': 'https://auth.example/authorize',
        'authorizationGrants': ['authorization_code'],
        'scopes': ['read', 'write'],
    }
    assert dropped == []


def test_oauth1_settings_are_ramls_own() -> None:
    scheme = Legacy(
        request_token_uri='https://a.example/request',
        authorization_uri='https://a.example/authorize',
        token_credentials_uri='https://a.example/credentials',
        signatures=['HMAC-SHA1'],
    )
    document, _ = rendered(secured_app({'legacy': scheme}, Open))
    assert document['securitySchemes']['legacy']['settings'] == {
        'requestTokenUri': 'https://a.example/request',
        'authorizationUri': 'https://a.example/authorize',
        'tokenCredentialsUri': 'https://a.example/credentials',
        'signatures': ['HMAC-SHA1'],
    }


def test_a_custom_scheme_must_be_named_as_raml_names_one() -> None:
    with pytest.raises(TypeError, match="must begin with 'x-'"):

        class Wrong(CustomScheme):
            raml_type = 'custom'

            async def authenticate(self, request: web.Request) -> str:
                return ''


class Guarded(RamlView):
    @secured('oauth', scopes=['read'])
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


def test_secured_becomes_secured_by_with_its_scopes() -> None:
    scheme = Books(access_token_uri='t', grants=['authorization_code'], scopes=['read'])
    document, dropped = rendered(secured_app({'oauth': scheme}, Guarded))
    assert document['/x']['get']['securedBy'] == [{'oauth': {'scopes': ['read']}}]
    assert dropped == []


class Either(RamlView):
    @secured('oauth', scopes=['read'])
    @secured('basic')
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


def test_stacked_secured_is_the_list_of_alternatives_raml_means() -> None:
    schemes = {'oauth': Books(access_token_uri='t', grants=['authorization_code']), 'basic': Basic()}
    document, dropped = rendered(secured_app(schemes, Either))
    assert document['/x']['get']['securedBy'] == [{'oauth': {'scopes': ['read']}}, 'basic']
    assert dropped == []


class Roled(RamlView):
    @secured('basic', scopes=['admin'])
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


def test_scopes_on_a_non_oauth_scheme_are_reported_not_written() -> None:
    """RAML's scopes belong to OAuth 2.0; a Basic scheme has no place for them."""
    document, dropped = rendered(secured_app({'basic': Basic()}, Roled))
    assert document['/x']['get']['securedBy'] == ['basic']
    assert any('scopes belong to OAuth 2.0' in entry for entry in dropped)


class Unregistered(RamlView):
    @secured('nobody')
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


def test_secured_by_never_names_a_scheme_the_document_does_not_declare() -> None:
    """An entry pointing at nothing is a document that will not parse."""
    document, dropped = rendered(one_view('/x', Unregistered))
    assert 'securedBy' not in document['/x']['get']
    assert any('no scheme of that name' in entry for entry in dropped)


def test_an_app_with_no_security_declares_none() -> None:
    document, dropped = rendered(one_view('/x', Open))
    assert 'securitySchemes' not in document
    assert dropped == []


# -- what is left out -----------------------------------------------------------


def test_an_undecorated_handler_is_described_by_its_path_alone() -> None:
    async def health(request: web.Request) -> web.Response:
        return web.Response()

    app = web.Application()
    app.router.add_get('/health', health)
    document, dropped = rendered(app)
    assert document['/health']['get'] == {}
    assert any('not decorated with @validate' in entry for entry in dropped)


def test_exclude_keeps_a_handler_out_of_the_description() -> None:
    @exclude
    async def health(request: web.Request) -> web.Response:
        return web.Response()

    app = web.Application()
    app.router.add_get('/health', health)
    document, dropped = rendered(app)
    assert '/health' not in document
    assert dropped == []


def test_a_static_mount_is_reported_rather_than_described(tmp_path: Any) -> None:
    app = web.Application()
    app.router.add_static('/files/', tmp_path)
    document, dropped = rendered(app)
    assert not [key for key in document if key.startswith('/')]
    assert any('not an API operation' in entry for entry in dropped)


def test_an_excluded_static_mount_is_silent(tmp_path: Any) -> None:
    app = web.Application()
    exclude(app.router.add_static('/files/', tmp_path))
    document, dropped = rendered(app)
    assert not [key for key in document if key.startswith('/')]
    assert dropped == []


def test_a_function_handler_is_described_like_a_view_method() -> None:
    @validate
    async def listing(
        title: str | None = None,
    ) -> Annotated[web.Response, Responds(200, list[Book])]:
        """Every book."""

    app = web.Application()
    app.router.add_get('/books', listing)
    document, dropped = rendered(app)
    assert document['/books']['get']['description'] == 'Every book.'
    assert list(document['/books']['get']['queryParameters']) == ['title']
    assert document['/books']['get']['responses']['200']['body'] == {'application/json': 'Book[]'}
    assert dropped == []


# -- multipart -----------------------------------------------------------------


class Meta(BaseModel):
    title: str


class UploadView(RamlView):
    async def post(
        self,
        meta: Meta,
        note: Annotated[str, Body()] = '',
        cover: Annotated[UploadedFile, File(file_types=['image/png'], max_size=5000, min_size=1)] = None,
    ) -> Annotated[web.Response, Responds(201, Book)]: ...


def test_an_upload_is_a_multipart_body_with_ramls_file_type() -> None:
    document, dropped = rendered(one_view('/upload', UploadView))
    body = document['/upload']['post']['body']
    assert list(body) == ['multipart/form-data']
    assert body['multipart/form-data']['properties'] == {
        'meta': 'Meta',
        'note': {'type': 'string', 'required': False},
        'cover': {
            'type': 'file',
            'minLength': 1,
            'maxLength': 5000,
            'fileTypes': ['image/png'],
            'required': False,
        },
    }
    assert dropped == []


class Mixed(RamlView):
    async def post(
        self, meta: Meta, cover: UploadedFile, note: str = ''
    ) -> Annotated[web.Response, Responds(201, Book)]: ...


def test_a_scalar_beside_an_upload_is_a_query_parameter_unless_marked() -> None:
    """Inference does not change inside a multipart handler; `Body()` says form field."""
    document, _ = rendered(one_view('/x', Mixed))
    assert list(document['/x']['post']['queryParameters']) == ['note']
    assert list(document['/x']['post']['body']['multipart/form-data']['properties']) == ['meta', 'cover']


def test_a_file_with_no_facets_is_just_the_file_type() -> None:
    class Plain(RamlView):
        async def post(self, cover: UploadedFile) -> Annotated[web.Response, Responds(201, Book)]: ...

    document, _ = rendered(one_view('/x', Plain))
    assert document['/x']['post']['body']['multipart/form-data']['properties'] == {'cover': 'file'}


# -- the validation 400 ---------------------------------------------------------


def test_an_operation_taking_a_parameter_declares_the_400_it_can_answer() -> None:
    document, _ = rendered(one_view('/books/{isbn}', ParamsView))
    response = document['/books']['/{isbn}']['get']['responses']['400']
    assert response == {
        'description': 'the request did not validate',
        'body': {'application/json': 'RequestError[]'},
    }


def test_the_error_type_is_declared_once_and_named() -> None:
    document, _ = rendered(one_view('/books/{isbn}', ParamsView))
    assert document['types']['RequestError']['properties']['in']['type'] == 'string'


class NoParameters(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


def test_an_operation_taking_nothing_cannot_fail_validation_and_declares_no_400() -> None:
    document, _ = rendered(one_view('/x', NoParameters))
    assert list(document['/x']['get']['responses']) == ['204']


class OwnError(RamlView):
    async def get(self, n: int = 0) -> Annotated[web.Response, Responds(200, Book), Responds(400, Error, 'mine')]: ...


def test_a_handler_declaring_its_own_400_keeps_it() -> None:
    """The document says what the handler says; nothing is added over the top."""
    document, _ = rendered(one_view('/x', OwnError))
    assert document['/x']['get']['responses']['400'] == {
        'description': 'mine',
        'body': {'application/json': 'Error'},
    }


class Nested(RamlView):
    async def post(self, isbn: str, /) -> Annotated[web.Response, Responds(201, Book)]: ...


def test_a_uri_parameter_sits_on_the_resource_whose_segment_names_it() -> None:
    """Not on the leaf: RAML rejects a `uriParameters` entry naming no template
    in *that* resource's relative URI, and `/books/{isbn}/cover` has three."""
    document, dropped = rendered(one_view('/books/{isbn}/cover', Nested))
    assert document['/books']['/{isbn}']['uriParameters'] == {'isbn': 'string'}
    assert 'uriParameters' not in document['/books']['/{isbn}']['/cover']
    assert 'post' in document['/books']['/{isbn}']['/cover']
    assert dropped == []


class Stray(RamlView):
    async def get(self, nowhere: Annotated[str, UriParam()] = 'x') -> Annotated[web.Response, Responds(204)]: ...


def test_a_uri_parameter_naming_no_segment_is_reported() -> None:
    document, dropped = rendered(one_view('/books', Stray))
    assert 'uriParameters' not in document['/books']
    assert any('names no segment' in entry for entry in dropped)


async def plain_any(request: web.Request) -> web.Response:
    return web.Response()


def test_a_function_registered_for_every_method_is_reported() -> None:
    """RAML has no wildcard method; `*:` is not a node a parser accepts."""
    app = web.Application()
    app.router.add_route('*', '/x', validate.and_request(plain_any))
    document, dropped = rendered(app)
    assert '/x' not in document
    assert any('no wildcard method' in entry for entry in dropped)


class TwoVerbs(RamlView):
    async def get(self, isbn: str, /) -> Annotated[web.Response, Responds(204)]: ...
    async def delete(self, isbn: str, /) -> Annotated[web.Response, Responds(204)]: ...


def test_verbs_sharing_a_path_declare_its_parameter_once() -> None:
    document, dropped = rendered(one_view('/books/{isbn}', TwoVerbs))
    assert document['/books']['/{isbn}']['uriParameters'] == {'isbn': 'string'}
    assert sorted(k for k in document['/books']['/{isbn}'] if not k.startswith('/')) == [
        'delete',
        'get',
        'uriParameters',
    ]
    assert dropped == []


class Summarised(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(204)]:
        """One book.

        The full prose, which RAML keeps separate from the short label
        because a reader scanning a list of methods wants only the label.
        """


def test_a_docstring_with_a_body_splits_into_display_name_and_description() -> None:
    """The author separated summary from body, so PEP 257's division applies."""
    method = rendered(one_view('/x', Summarised))[0]['/x']['get']
    assert method['displayName'] == 'One book.'
    assert method['description'].startswith('The full prose,')
    assert 'label.' in method['description']


class OneLiner(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(204)]:
        """List them."""


def test_a_one_line_docstring_is_the_description() -> None:
    """Most one-liners are prose, not a name the author chose for the method."""
    method = rendered(one_view('/x', OneLiner))[0]['/x']['get']
    assert method['description'] == 'List them.'
    assert 'displayName' not in method


class Undocumented(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(204)]: ...


def test_a_handler_with_no_docstring_gets_neither_node() -> None:
    method = rendered(one_view('/x', Undocumented))[0]['/x']['get']
    assert 'displayName' not in method
    assert 'description' not in method


def test_documentation_is_written_at_the_root() -> None:
    from aiohttp_raml import Documentation

    document, dropped = rendered(
        one_view('/x', OneLiner),
        documentation=[Documentation(title='Rate limits', content='Ten per second.')],
    )
    assert document['documentation'] == [{'title': 'Rate limits', 'content': 'Ten per second.'}]
    assert dropped == []


def test_no_documentation_writes_no_node() -> None:
    assert 'documentation' not in rendered(one_view('/x', OneLiner))[0]
