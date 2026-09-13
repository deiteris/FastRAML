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


def test_the_stub_names_both_documents(client: TestClient) -> None:
    body = client.get('/raml-docs').text
    assert '/raml.json' in body
    assert 'No viewer is configured' in body


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


def test_a_viewer_url_is_linked_with_the_document(client: Any) -> None:  # noqa: ARG001 - module fixture ordering
    fresh = build_app()
    add_raml_routes(fresh, viewer_url='/viewer/index.html')
    body = TestClient(fresh).get('/raml-docs').text
    assert '/viewer/index.html?src=/raml.json' in body


def test_docs_url_none_adds_no_stub() -> None:
    fresh = build_app()
    add_raml_routes(fresh, docs_url=None)
    local = TestClient(fresh)
    assert local.get('/raml.json').status_code == 200
    assert local.get('/raml-docs').status_code == 404
