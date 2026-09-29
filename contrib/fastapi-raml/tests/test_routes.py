"""Which routes are described: an included router's among them, a mounted app's not.

FastAPI 0.139 stopped copying an included router's routes into `app.routes`,
and a renderer reading that list described an app built from routers -- most
of them -- as having no endpoints at all, and said nothing.
"""

import fastapi
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fastapi_raml import add_raml_routes
from tests.support import dropped, parsed


def test_an_included_routers_routes_are_described_under_its_prefix() -> None:
    inner = APIRouter(prefix='/books')

    @inner.get('/{isbn}')
    def book(isbn: str) -> int: ...

    outer = APIRouter(prefix='/v1')
    outer.include_router(inner)
    app = FastAPI(title='R')
    app.include_router(outer, prefix='/api')

    assert list(parsed(app).endpoints) == ['/api', '/api/v1', '/api/v1/books', '/api/v1/books/{isbn}']
    assert dropped(app) == []


def test_a_route_left_out_of_the_schema_is_left_out_here() -> None:
    router = APIRouter()

    @router.get('/hidden', include_in_schema=False)
    def hidden() -> int: ...

    @router.get('/shown')
    def shown() -> int: ...

    app = FastAPI(title='R')
    app.include_router(router)
    assert list(parsed(app).endpoints) == ['/shown']


def test_a_mounted_application_is_reported_and_not_described() -> None:
    """It has its own schema; `get_openapi` leaves it out as well."""
    sub = FastAPI()

    @sub.get('/x')
    def x() -> int: ...

    app = FastAPI(title='R')
    app.mount('/sub', sub)
    assert any(entry.startswith('/sub: a mounted application') for entry in dropped(app))


def test_the_viewer_mount_is_not_reported_as_an_application() -> None:
    """Static files have no routes, and are not an API left undescribed."""
    app = FastAPI(title='R')

    @app.get('/x')
    def x() -> int: ...

    add_raml_routes(app)
    assert dropped(app) == []


lazy = pytest.mark.skipif(
    not hasattr(fastapi.routing, 'iter_route_contexts'),
    reason='before FastAPI 0.139 a route added to a router after inclusion is not served',
)


@lazy
def test_a_route_added_to_an_included_router_later_shows_up() -> None:
    """The serve cache keys on the routes it can see, an included router's too."""
    router = APIRouter()

    @router.get('/first')
    def first() -> int: ...

    app = FastAPI(title='R')
    app.include_router(router)
    add_raml_routes(app, mount_viewer=None)
    client = TestClient(app)
    assert '/later' not in client.get('/raml.json').json()['endpoints']

    @router.get('/later')
    def later() -> int: ...

    assert '/later' in client.get('/raml.json').json()['endpoints']


def test_a_method_raml_has_no_node_for_is_reported_and_left_out() -> None:
    """`PURGE:` under a resource is an unknown key; the document would not parse."""
    app = FastAPI(title='R')

    @app.api_route('/cache', methods=['GET', 'PURGE'])
    def cache() -> int: ...

    assert list(parsed(app).endpoints['/cache'].operations) == ['get']
    assert dropped(app) == ['PURGE /cache: RAML has no PURGE method; not described']


def test_a_route_with_only_such_methods_leaves_no_empty_resource() -> None:
    app = FastAPI(title='R')

    @app.api_route('/cache', methods=['PURGE'])
    def cache() -> int: ...

    assert '/cache' not in parsed(app).endpoints
