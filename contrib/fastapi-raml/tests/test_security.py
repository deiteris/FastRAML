"""Every scheme `fastapi.security` offers renders, and `securedBy` names only declared ones.

A `securedBy` entry naming an undeclared scheme does not parse, so one scheme
the renderer could not express used to take the whole document down with it.
"""

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Security
from fastapi.dependencies.models import Dependant
from fastapi.openapi.models import OAuthFlowImplicit, OAuthFlowPassword, OAuthFlows
from fastapi.security import (
    APIKeyCookie,
    APIKeyHeader,
    APIKeyQuery,
    HTTPBasic,
    HTTPBearer,
    HTTPDigest,
    OAuth2,
    OAuth2AuthorizationCodeBearer,
    OAuth2PasswordBearer,
    OpenIdConnect,
)
from fastapi.security.base import SecurityBase

from tests.support import dropped, operation, parsed

SCHEMES = [
    (HTTPBearer(), 'Pass Through', {'headers': ['Authorization']}),
    (HTTPBasic(), 'Basic Authentication', {}),
    (HTTPDigest(), 'Digest Authentication', {}),
    (APIKeyHeader(name='X-Key'), 'Pass Through', {'headers': ['X-Key']}),
    (APIKeyQuery(name='key'), 'Pass Through', {'queryParameters': ['key']}),
    (APIKeyCookie(name='sid'), 'Pass Through', {'headers': ['Cookie']}),
    (OAuth2PasswordBearer(tokenUrl='/token'), 'OAuth 2.0', {}),
    (OAuth2AuthorizationCodeBearer(authorizationUrl='https://a', tokenUrl='https://t'), 'OAuth 2.0', {}),
    (OpenIdConnect(openIdConnectUrl='https://id/.well-known'), 'x-openid-connect', {'headers': ['Authorization']}),
]


def secured_by(scheme: Any) -> FastAPI:
    app = FastAPI(title='S')

    @app.get('/me')
    def me(credential: Annotated[Any, Depends(scheme)]) -> int: ...

    return app


@pytest.mark.parametrize(('scheme', 'kind', 'carried'), SCHEMES, ids=[type(s).__name__ for s, _, _ in SCHEMES])
def test_every_fastapi_scheme_is_declared_and_referenced(scheme: Any, kind: str, carried: dict[str, list[str]]) -> None:
    raml = parsed(secured_by(scheme))
    declared = raml.entry_point.security_schemes[scheme.scheme_name]
    assert declared.type == kind
    for node, names in carried.items():
        described = declared.described_by
        assert list(getattr(described, 'headers' if node == 'headers' else 'query_parameters')) == names
    assert [entry.name for entry in raml.endpoints['/me'].operations['get'].secured_by] == [scheme.scheme_name]
    assert dropped(secured_by(scheme)) == []


def test_openid_connect_names_its_discovery_url() -> None:
    raml = parsed(secured_by(OpenIdConnect(openIdConnectUrl='https://id/.well-known')))
    assert 'https://id/.well-known' in raml.entry_point.security_schemes['OpenIdConnect'].description.value


def test_every_oauth2_flow_is_a_grant() -> None:
    """RAML states grants as a list beside one pair of URIs; the first flow did not speak for all."""
    flows = OAuthFlows(
        implicit=OAuthFlowImplicit(authorizationUrl='https://a', scopes={'read': 'r'}),
        password=OAuthFlowPassword(tokenUrl='https://t', scopes={'write': 'w'}),
    )
    raml = parsed(secured_by(OAuth2(flows=flows)))
    settings = raml.entry_point.security_schemes['OAuth2'].settings
    assert settings.lists['authorizationGrants'] == ['implicit', 'password']
    assert settings.scopes == ['read', 'write']


class _Unknown(SecurityBase):
    """A scheme kind this renderer has no RAML for."""

    def __init__(self) -> None:
        self.model = type('Model', (), {'type_': 'mutualTLS', 'description': None})()
        self.scheme_name = 'Mutual'

    def __call__(self) -> None: ...


def test_a_scheme_that_cannot_be_declared_is_not_referenced() -> None:
    """A `securedBy` naming an undeclared scheme does not parse."""
    app = secured_by(_Unknown())
    raml = parsed(app)
    assert raml.endpoints['/me'].operations['get'].secured_by == []
    assert any('mutualTLS' in entry for entry in dropped(app))


def test_two_enforcing_schemes_are_reported_as_both_required() -> None:
    """FastAPI runs each; RAML reads a `securedBy` list as alternatives."""
    app = FastAPI(title='S')

    @app.get('/me')
    def me(a: Annotated[Any, Depends(HTTPBearer())], b: Annotated[Any, Depends(APIKeyHeader(name='K'))]) -> int: ...

    assert any('each required' in entry for entry in dropped(app))


def test_schemes_that_do_not_refuse_also_admit_an_anonymous_call() -> None:
    """`auto_error=False` hands a missing credential to the handler: RAML's `null` entry."""
    app = FastAPI(title='S')

    @app.get('/me')
    def me(
        a: Annotated[Any, Depends(HTTPBearer(auto_error=False))],
        b: Annotated[Any, Depends(APIKeyHeader(name='K', auto_error=False))],
    ) -> int: ...

    assert dropped(app) == []
    assert [scheme.name for scheme in operation(app, '/me', 'get').secured_by] == ['HTTPBearer', 'APIKeyHeader', 'null']


def test_one_scheme_that_refuses_admits_no_anonymous_call() -> None:
    app = FastAPI(title='S')

    @app.get('/me')
    def me(
        a: Annotated[Any, Depends(HTTPBearer())],
        b: Annotated[Any, Depends(APIKeyHeader(name='K', auto_error=False))],
    ) -> int: ...

    assert 'null' not in [scheme.name for scheme in operation(app, '/me', 'get').secured_by]


def test_two_schemes_sharing_a_name_are_declared_apart() -> None:
    """FastAPI names a scheme after its class, so two API-key headers share `APIKeyHeader`."""
    app = FastAPI(title='S')

    @app.get('/a')
    def a(key: Annotated[Any, Depends(APIKeyHeader(name='X-A'))]) -> int: ...

    @app.get('/b')
    def b(key: Annotated[Any, Depends(APIKeyHeader(name='X-B'))]) -> int: ...

    @app.get('/c')
    def c(key: Annotated[Any, Depends(APIKeyHeader(name='X-A'))]) -> int: ...

    raml = parsed(app)
    declared = raml.entry_point.security_schemes
    assert list(declared) == ['APIKeyHeader', 'APIKeyHeader_2']
    assert [raml.endpoints[path].operations['get'].secured_by[0].name for path in ('/a', '/b', '/c')] == [
        'APIKeyHeader',
        'APIKeyHeader_2',
        'APIKeyHeader',
    ]
    assert dropped(app) == [
        "securitySchemes: two schemes are named 'APIKeyHeader'; the second is declared as APIKeyHeader_2"
    ]


def test_a_scope_the_scheme_does_not_list_is_declared_on_it() -> None:
    """FastAPI lets a route ask for any scope; RAML refuses one its scheme does not declare."""
    app = FastAPI(title='S')
    listed = OAuth2PasswordBearer(tokenUrl='/token', scopes={'read': 'r'})

    @app.get('/me')
    def me(token: Annotated[str, Security(listed, scopes=['admin'])]) -> int: ...

    raml = parsed(app)
    assert raml.entry_point.security_schemes['OAuth2PasswordBearer'].settings.scopes == ['read', 'admin']
    assert dropped(app) == []


oauth = OAuth2PasswordBearer(tokenUrl='/token', scopes={'read': 'r', 'write': 'w'})


def reader(token: Annotated[str, Security(oauth, scopes=['read'])]) -> str:
    return token


def test_scopes_asked_for_at_two_levels_are_one_entry() -> None:
    app = FastAPI(title='S')

    @app.get('/me')
    def me(user: Annotated[str, Security(reader, scopes=['write'])]) -> int: ...

    (entry,) = operation(app, '/me', 'get').secured_by
    assert sorted(entry.compiled_params or ()) == ['read', 'write']


@pytest.mark.skipif(
    hasattr(Dependant(), 'security_requirements'),
    reason='FastAPI releases with the older dependency layout keep no scopes for a scheme that is not OAuth',
)
def test_scopes_on_a_scheme_that_is_not_oauth_are_reported() -> None:
    app = FastAPI(title='S')

    @app.get('/me')
    def me(key: Annotated[str, Security(APIKeyHeader(name='K'), scopes=['admin'])]) -> int: ...

    assert any('RAML scopes belong to OAuth 2.0' in entry for entry in dropped(app))
    parsed(app)


def test_router_level_security_reaches_each_route() -> None:
    from fastapi import APIRouter

    router = APIRouter(prefix='/v1', dependencies=[Depends(HTTPBearer())])

    @router.get('/me')
    def me() -> int: ...

    app = FastAPI(title='S')
    app.include_router(router)
    assert [entry.name for entry in operation(app, '/v1/me', 'get').secured_by] == ['HTTPBearer']
