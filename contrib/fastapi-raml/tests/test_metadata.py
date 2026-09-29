"""What the app and its routes say about themselves, beside their shapes.

Each was dropped without a word: a deprecation, the tags, an operation id,
the contact and licence, a proxy's `root_path`. RAML carries all of them --
the root's `documentation:`, and annotations for what it has no node for.
"""

from enum import StrEnum
from typing import Annotated, Any

from fastapi import Body, FastAPI, Query
from pydantic import BaseModel, Field

from fastapi_raml import render
from tests.support import dropped, parsed


def rendered(app: FastAPI) -> dict[str, Any]:
    parsed(app)  # the document parses, annotation types and all
    return render(app).document.render()


class Kind(StrEnum):
    BOOKS = 'books'


def test_a_deprecated_route_its_tags_and_its_operation_id_are_annotations() -> None:
    app = FastAPI(title='M')

    @app.get('/old', deprecated=True, tags=[Kind.BOOKS, 'legacy'], operation_id='getOld')
    def old() -> int: ...

    get = rendered(app)['/old']['get']
    assert (get['(deprecated)'], get['(tags)'], get['(operationId)']) == (None, ['books', 'legacy'], 'getOld')
    assert dropped(app) == []


def test_only_what_a_route_says_is_annotated() -> None:
    """FastAPI generates an operation id for every route; only one the app chose is its name."""
    app = FastAPI(title='M')

    @app.get('/plain')
    def plain() -> int: ...

    document = rendered(app)
    assert not any(key.startswith('(') for key in document['/plain']['get'])
    assert 'annotationTypes' not in document


class Legacy(BaseModel):
    shelf: str = Field(title='Shelf', deprecated='use isbn')


def test_a_deprecated_parameter_and_field_and_a_title() -> None:
    app = FastAPI(title='M')

    @app.post('/legacy')
    def legacy(body: Legacy, q: Annotated[str | None, Query(deprecated=True)] = None) -> int: ...

    document = rendered(app)
    assert document['/legacy']['post']['queryParameters']['q']['(deprecated)'] is None
    shelf = document['types']['Legacy']['properties']['shelf']
    assert (shelf['displayName'], shelf['(deprecated)']) == ('Shelf', 'use isbn')


class Item(BaseModel):
    n: int


def test_named_examples_are_examples_and_are_checked() -> None:
    """`openapi_examples` is FastAPI's; the parse validates each against the type."""
    app = FastAPI(title='M')

    @app.post('/items')
    def add(item: Annotated[Item, Body(openapi_examples={'small': {'summary': 's', 'value': {'n': 1}}})]) -> int: ...

    assert rendered(app)['/items']['post']['body']['application/json']['examples'] == {'small': {'n': 1}}


def test_the_apps_own_description_of_itself() -> None:
    app = FastAPI(
        title='M',
        summary='Short.',
        description='Long.',
        contact={'name': 'Ada', 'email': 'ada@example.org'},
        license_info={'name': 'MIT', 'identifier': 'MIT'},
        terms_of_service='https://example.org/terms',
        openapi_tags=[{'name': 'books', 'description': 'The shelf.'}, {'name': 'bare'}],
    )
    document = rendered(app)
    assert document['description'] == 'Short.\n\nLong.'
    assert document['documentation'] == [
        {'title': 'Contact', 'content': 'Ada\n\n<ada@example.org>'},
        {'title': 'License', 'content': 'MIT (MIT)'},
        {'title': 'Terms of service', 'content': 'https://example.org/terms'},
        {'title': 'Tags', 'content': '- **books**: The shelf.'},
    ]


def test_a_root_path_is_where_the_paths_are() -> None:
    """Behind a proxy stripping `/api`; FastAPI's schema lists it as the first server."""
    app = FastAPI(title='M', root_path='/api')
    assert rendered(app)['baseUri'] == '/api'
    served = FastAPI(title='M', root_path='/api', servers=[{'url': 'https://example.org'}])
    assert rendered(served)['baseUri'] == '/api'
    assert dropped(served) == ['servers: 1 extra server(s); RAML has one baseUri']
