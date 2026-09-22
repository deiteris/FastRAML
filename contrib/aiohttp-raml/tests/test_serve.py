"""The routes `add_raml_routes` adds, over HTTP."""

from __future__ import annotations

import builtins
from typing import Annotated, Any

import pytest
from aiohttp import web
from pydantic import BaseModel

from aiohttp_raml import RamlView, Responds, add_raml_routes, build
from aiohttp_raml.serve import RAML_MEDIA_TYPE
from examples import server


class Thing(BaseModel):
    a: str


class ThingView(RamlView):
    async def get(self) -> Annotated[web.Response, Responds(200, Thing)]:
        return web.json_response({'a': 'x'})


def build_app() -> web.Application:
    """A fresh app per test. aiohttp freezes a router once its app has served."""
    app = web.Application()
    app.router.add_view('/thing', ThingView)
    return app


@pytest.fixture
async def client(aiohttp_client: Any) -> Any:
    return await aiohttp_client(server.build_app())


async def test_the_api_itself_still_answers(client: Any) -> None:
    assert (await client.get('/books')).status == 200
    assert (await (await client.get('/books/9780306406157')).json())['title'] == 'The Hobbit'
    assert (await client.get('/books/0000000000000')).status == 404


async def test_raml_source_is_served_as_raml(client: Any) -> None:
    response = await client.get('/raml')
    assert response.status == 200
    assert response.headers['content-type'].startswith(RAML_MEDIA_TYPE)
    assert (await response.text()).startswith('#%RAML 1.0\n')


async def test_the_tree_is_what_the_viewer_reads(client: Any) -> None:
    # `viewer/src/load.ts` refuses anything missing these four keys by name.
    tree = await (await client.get('/raml.json')).json()
    for key in ('types', 'endpoints', 'annotation_types', 'security_schemes'):
        assert isinstance(tree[key], dict), key
    assert sorted(tree['endpoints']) == ['/books', '/books/{isbn}', '/books/{isbn}/cover']
    # `RequestError` is there because every operation taking a parameter
    # declares the 400 this package answers with.
    assert sorted(next(iter(tree['types'].values()))) == ['Book', 'Error', 'RequestError']


async def test_the_describing_routes_do_not_describe_themselves(client: Any) -> None:
    """aiohttp has no `include_in_schema`, so `exclude` is what keeps them out."""
    tree = await (await client.get('/raml.json')).json()
    assert '/raml' not in tree['endpoints']
    assert '/raml.json' not in tree['endpoints']
    assert '/raml-viewer' not in tree['endpoints']


async def test_this_package_serves_no_html_of_its_own(client: Any) -> None:
    for url in ('/raml', '/raml.json'):
        assert 'text/html' not in (await client.get(url)).headers['content-type']


def test_the_rendered_document_loses_nothing() -> None:
    """The worked example renders with nothing dropped, and the gate says so."""
    assert build(server.build_app(), title='Library').dropped == []


# -- the viewer ----------------------------------------------------------------


async def test_the_bundled_viewer_is_mounted_and_serves_its_index(client: Any) -> None:
    """The whole point of the `viewer` extra: no checkout, no `npm run build`."""
    response = await client.get('/raml-viewer/')
    assert response.status == 200
    text = await response.text()
    assert '<div id="root">' in text
    # Relative asset paths, so the mount path is not baked into the bundle.
    assert './assets/' in text


async def test_the_mounted_viewer_reads_this_app_and_not_its_own_sample(client: Any) -> None:
    """The bundle ships `api.json` -- the worked bookstore -- so it demos alone.

    Mounted under a real app that sample would answer instead of the app's own
    description: a convincing wrong answer rather than a visible failure. The
    route registered before the static files shadows it.
    """
    served = await (await client.get('/raml-viewer/api.json')).json()
    assert served == await (await client.get('/raml.json')).json()
    assert sorted(served['endpoints']) == ['/books', '/books/{isbn}', '/books/{isbn}/cover']


async def test_a_custom_mount_path_carries_its_own_document(aiohttp_client: Any) -> None:
    app = add_raml_routes(build_app(), mount_viewer='/ui', title='Fresh')
    local = await aiohttp_client(app)
    assert (await local.get('/ui/')).status == 200
    assert await (await local.get('/ui/api.json')).json() == await (await local.get('/raml.json')).json()


async def test_mount_viewer_none_leaves_it_off(aiohttp_client: Any) -> None:
    app = add_raml_routes(build_app(), mount_viewer=None, title='Fresh')
    local = await aiohttp_client(app)
    assert (await local.get('/raml-viewer/')).status == 404
    assert (await local.get('/raml.json')).status == 200


async def test_without_the_package_nothing_is_mounted_and_nothing_fails(aiohttp_client: Any, monkeypatch: Any) -> None:
    """A missing frontend must not stop an app serving its own RAML."""
    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == 'fastraml_viewer':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', refuse)
    app = add_raml_routes(build_app(), title='Fresh')
    monkeypatch.undo()
    local = await aiohttp_client(app)
    assert (await local.get('/raml-viewer/')).status == 404
    # The two routes that carry the content are unaffected.
    assert (await local.get('/raml.json')).status == 200
    assert (await local.get('/raml')).status == 200


# -- the cache ------------------------------------------------------------------


async def test_the_document_is_built_once_and_held(aiohttp_client: Any, monkeypatch: Any) -> None:
    """There is no cache key because aiohttp freezes the router at startup."""
    builds = []
    import aiohttp_raml.serve as module

    real_build = module.build

    def counted(app: Any, **metadata: Any) -> Any:
        builds.append(app)
        return real_build(app, **metadata)

    monkeypatch.setattr(module, 'build', counted)
    local = await aiohttp_client(add_raml_routes(build_app(), title='Fresh'))
    for _ in range(3):
        assert (await local.get('/raml.json')).status == 200
    assert len(builds) == 1


async def test_a_route_added_after_the_app_starts_is_refused_by_aiohttp(aiohttp_client: Any) -> None:
    """Why there is no cache key: the router cannot change once it is frozen."""
    local = await aiohttp_client(add_raml_routes(build_app(), title='Fresh'))
    with pytest.raises(RuntimeError, match='frozen'):
        local.app.router.add_view('/later', ThingView)
