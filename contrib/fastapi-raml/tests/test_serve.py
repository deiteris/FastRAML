"""The routes `add_raml_routes` adds, over HTTP."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from examples.server import app
from fastapi_raml import add_raml_routes
from fastapi_raml.serve import RAML_MEDIA_TYPE


@pytest.fixture(scope='module')
def client() -> TestClient:
    return TestClient(app)


def test_the_api_itself_still_answers(client: TestClient) -> None:
    assert client.get('/books').status_code == 200
    assert client.get('/books/9780306406157').json()['title'] == 'The Hobbit'
    assert client.get('/books/0000000000000').status_code == 404


def test_raml_source_is_served_as_raml(client: TestClient) -> None:
    response = client.get('/raml')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith(RAML_MEDIA_TYPE)
    assert response.text.startswith('#%RAML 1.0\n')


def test_the_tree_is_what_the_viewer_reads(client: TestClient) -> None:
    # `viewer/src/load.ts` refuses anything missing these four keys by name.
    tree = client.get('/raml.json').json()
    for key in ('types', 'endpoints', 'annotation_types', 'security_schemes'):
        assert isinstance(tree[key], dict), key
    assert sorted(tree['endpoints']) == ['/books', '/books/{isbn}']
    assert sorted(next(iter(tree['types'].values()))) == ['Book', 'Error']


def test_this_package_serves_no_html_of_its_own(client: TestClient) -> None:
    """Rendering belongs to `fastraml-viewer`, not here.

    The stub that used to live at `/raml-docs` existed only to compose
    `?src=/raml.json` for a viewer that read its document from a query string.
    The viewer reads `api.json` beside itself now, so the link had nothing left
    to say.
    """
    assert client.get('/raml-docs').status_code == 404
    for url in ('/raml', '/raml.json'):
        assert 'text/html' not in client.get(url).headers['content-type']


def test_the_bundled_viewer_is_mounted_and_serves_its_index(client: TestClient) -> None:
    """The whole point of the `viewer` extra: no checkout, no `npm run build`."""
    response = client.get('/raml-viewer/')
    assert response.status_code == 200
    assert '<div id="root">' in response.text
    # Relative asset paths, so the mount path is not baked into the bundle.
    assert './assets/' in response.text


def test_the_bare_mount_path_redirects_to_the_slash(client: TestClient) -> None:
    """Served without the slash, the bundle's `./assets/` resolve above the mount.

    Starlette's `redirect_slashes` is what does it; this pins that it still does.
    """
    response = client.get('/raml-viewer', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'].endswith('/raml-viewer/')


def test_without_the_package_nothing_is_mounted_and_nothing_fails(monkeypatch: Any) -> None:
    """A missing frontend must not stop an app serving its own RAML."""
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == 'fastraml_viewer':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', refuse)
    fresh = build_app()
    add_raml_routes(fresh)
    local = TestClient(fresh)
    assert local.get('/raml-viewer/').status_code == 404
    # The two routes that carry the content are unaffected.
    assert local.get('/raml.json').status_code == 200
    assert local.get('/raml').status_code == 200


def test_mount_viewer_none_leaves_it_off(client: Any) -> None:  # noqa: ARG001 - module fixture ordering
    fresh = build_app()
    add_raml_routes(fresh, mount_viewer=None)
    assert TestClient(fresh).get('/raml-viewer/').status_code == 404


def test_the_routes_stay_out_of_the_apps_own_schema(client: TestClient) -> None:
    paths = client.get('/openapi.json').json()['paths']
    assert '/raml' not in paths
    assert '/raml.json' not in paths


class Thing(BaseModel):
    a: str


class Other(BaseModel):
    b: int


def build_app() -> FastAPI:
    """A fresh app per test. Models are module-level so their forward refs resolve."""
    fresh = FastAPI(title='Fresh', version='1')

    @fresh.get('/thing')
    def thing() -> Thing: ...

    return fresh


def test_add_raml_routes_returns_the_app() -> None:
    fresh = build_app()
    assert add_raml_routes(fresh) is fresh


def test_a_route_added_after_wiring_still_appears() -> None:
    """The cache keys on the router's version, so a later route invalidates it."""
    fresh = build_app()
    add_raml_routes(fresh)
    local = TestClient(fresh)
    assert '/later' not in local.get('/raml.json').json()['endpoints']

    @fresh.get('/later')
    def later() -> Other: ...

    assert '/later' in local.get('/raml.json').json()['endpoints']


def test_the_mounted_viewer_reads_this_app_and_not_its_own_sample(client: TestClient) -> None:
    """The bundle ships `api.json` -- the worked bookstore -- so it demos alone.

    Mounted under a real app that sample would answer instead of the app's own
    description: a convincing wrong answer rather than a visible failure. The
    route registered before the mount shadows it.
    """
    served = client.get('/raml-viewer/api.json').json()
    assert served == client.get('/raml.json').json()
    assert sorted(served['endpoints']) == ['/books', '/books/{isbn}']


def test_a_custom_mount_path_carries_its_own_document(client: Any) -> None:  # noqa: ARG001 - module fixture ordering
    fresh = build_app()
    add_raml_routes(fresh, mount_viewer='/ui')
    local = TestClient(fresh)
    assert local.get('/ui/').status_code == 200
    assert local.get('/ui/api.json').json() == local.get('/raml.json').json()
