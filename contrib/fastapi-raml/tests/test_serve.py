"""The routes `add_raml_routes` adds, over HTTP."""

import re
from typing import Annotated, Any

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from raml_document import UNSET

from examples.server import app
from fastapi_raml import BuildError, add_raml_routes, build, render
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


def test_mount_viewer_none_leaves_it_off(client: Any) -> None:
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
    """The cache is rebuilt when the app's routes change, so a later route shows up."""
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


def test_a_custom_mount_path_carries_its_own_document(client: Any) -> None:
    fresh = build_app()
    add_raml_routes(fresh, mount_viewer='/ui')
    local = TestClient(fresh)
    assert local.get('/ui/').status_code == 200
    assert local.get('/ui/api.json').json() == local.get('/raml.json').json()


def test_a_server_variable_without_a_default_renders_none() -> None:
    """An absent default stays absent; `default: null` would be a value."""
    fresh = FastAPI(servers=[{'url': 'https://{host}/v1', 'variables': {'host': {'enum': ['a', 'b']}}}])
    assert render(fresh).document.base_uri_parameters['host'].default is UNSET


class Broken(BaseModel):
    """An example the model's own type refuses: RAML validates examples, pydantic does not."""

    n: int = Field(examples=['ten'])


def broken_app() -> FastAPI:
    fresh = FastAPI(title='Broken')

    @fresh.get('/broken')
    def broken() -> Broken: ...

    return fresh


def test_a_failed_build_answers_with_the_reason() -> None:
    """A bare 500 sent the reader to the server log for a problem in their own models."""
    fresh = broken_app()
    add_raml_routes(fresh, mount_viewer=None)
    for url in ('/raml', '/raml.json'):
        response = TestClient(fresh).get(url)
        assert response.status_code == 500
        assert 'does not parse' in response.text
        # Cited at its line in the rendered text, and that line quoted: the
        # text itself is not served while it fails.
        assert re.search(r'api\.raml:\d+:\d+ ', response.text)
        assert re.search(r'\n +\d+ \| ', response.text)


def test_build_raises_the_same_error() -> None:
    with pytest.raises(BuildError) as caught:
        build(broken_app())
    assert caught.value.text.startswith('#%RAML 1.0')
    assert 'Temp' not in str(caught.value), 'no throwaway path in the message'


def test_a_build_is_rendered_once_until_the_routes_change(monkeypatch: Any) -> None:
    """Failures included: rendering a failing document per request only fails slower."""
    from fastapi_raml import serve

    calls = []
    real = serve.render

    def counting(app: Any) -> Any:
        calls.append(app)
        return real(app)

    monkeypatch.setattr(serve, 'render', counting)
    for make in (build_app, broken_app):
        calls.clear()
        fresh = make()
        add_raml_routes(fresh, mount_viewer=None)
        local = TestClient(fresh)
        for _ in range(3):
            local.get('/raml')
            local.get('/raml.json')
        assert len(calls) == 1, make.__name__


def test_build_strict_refuses_what_it_had_to_leave_out() -> None:
    fresh = FastAPI(title='Lossy')

    @fresh.get('/x')
    def x(n: Annotated[int, Query(gt=0)]) -> int: ...

    assert build(fresh).dropped
    with pytest.raises(BuildError, match='leaves out'):
        build(fresh, strict=True)


def test_what_is_left_out_is_logged(caplog: Any) -> None:
    fresh = FastAPI(title='Lossy')

    @fresh.get('/x')
    def x(n: Annotated[int, Query(gt=0)]) -> int: ...

    with caplog.at_level('WARNING', logger='fastapi_raml'):
        build(fresh)
    assert any('exclusive minimum' in record.getMessage() for record in caplog.records)


def test_a_browser_is_shown_the_source_rather_than_handed_a_download(client: TestClient) -> None:
    browser = client.get('/raml', headers={'accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
    assert browser.headers['content-type'].startswith('text/plain')
    assert 'Accept' in browser.headers['vary']
    tool = client.get('/raml', headers={'accept': '*/*'})
    assert tool.headers['content-type'].startswith(RAML_MEDIA_TYPE)
    assert browser.text == tool.text


def test_adding_the_routes_twice_is_refused() -> None:
    """The second pair would be shadowed by the first, and serve nothing."""
    fresh = build_app()
    add_raml_routes(fresh, mount_viewer=None)
    with pytest.raises(ValueError, match='already routed'):
        add_raml_routes(fresh, mount_viewer=None)


def test_the_tree_is_served_as_built(client: TestClient) -> None:
    assert client.get('/raml.json').json() == build(app).tree
