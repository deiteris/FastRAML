"""Query, header and path parameters: where each lands, and whether it may be left out.

`test_omitting_a_parameter_agrees` is the differential for this half: FastAPI
answers the request without the parameter, and the RAML's `required` must
predict whether it refuses. RAML's default in every one of these positions is
`required: true`, so a renderer that reads only the annotation makes the
document stricter than the code -- in the direction no test that always sends
the parameter complains about.
"""

from typing import Annotated

import fastapi
import pytest
from fastapi import Depends, FastAPI, Header, Path, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from fastapi_raml import render
from tests.support import dropped, operation, parsed


def paging(limit: int = 10, offset: int = 0) -> tuple[int, int]:
    return limit, offset


app = FastAPI(title='P')


@app.get('/items/{item_id}')
def get_item(
    item_id: int,
    page: Annotated[tuple[int, int], Depends(paging)],
    must: str,
    x_key: Annotated[str, Header()],
    q: Annotated[str | None, Query(max_length=5)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    x_trace: Annotated[str | None, Header()] = None,
) -> int:
    return item_id


#: A value FastAPI accepts for each parameter the route declares.
VALID: dict[str, str] = {'limit': '1', 'offset': '1', 'must': 'a', 'q': 'a', 'tags': 'a'}
HEADERS: dict[str, str] = {'x-trace': 'a', 'x-key': 'a'}


def names() -> list[tuple[str, str]]:
    request = operation(app, '/items/{item_id}', 'get').request
    return [('query', name) for name in request.query_parameters] + [('header', name) for name in request.headers]


@pytest.mark.parametrize(('place', 'name'), names(), ids=[f'{place}:{name}' for place, name in names()])
def test_omitting_a_parameter_agrees(place: str, name: str) -> None:
    request = operation(app, '/items/{item_id}', 'get').request
    declared = (request.query_parameters if place == 'query' else request.headers)[name]
    query = {key: value for key, value in VALID.items() if not (place == 'query' and key == name)}
    headers = {key: value for key, value in HEADERS.items() if not (place == 'header' and key == name)}
    response = TestClient(app).get('/items/1', params=query, headers=headers)
    assert (response.status_code == 422) == declared.required, response.text


def test_every_request_the_differential_sends_is_otherwise_accepted() -> None:
    """Without this, a 422 for another reason would make every verdict agree."""
    assert TestClient(app).get('/items/1', params=VALID, headers=HEADERS).status_code == 200


def test_a_default_is_written() -> None:
    limit = operation(app, '/items/{item_id}', 'get').request.query_parameters['limit']
    assert limit.base.default.raw == 10


def test_an_optional_parameter_is_absent_rather_than_null() -> None:
    """A query value is text or missing; `str | None` means only that it may be left out."""
    declared = render(app).document.root.at('/items/{item_id}').methods['get'].query_parameters['q']
    assert declared.render() == {'type': 'string', 'maxLength': 5, 'required': False}
    q = operation(app, '/items/{item_id}', 'get').request.query_parameters['q']
    assert q.base.validate('abcdef') is not None


def test_a_header_is_named_as_it_travels() -> None:
    assert sorted(operation(app, '/items/{item_id}', 'get').request.headers) == ['x-key', 'x-trace']


def test_a_cookie_parameter_is_reported() -> None:
    from fastapi import Cookie

    local = FastAPI(title='C')

    @local.get('/c')
    def c(sid: Annotated[str | None, Cookie()] = None) -> int: ...

    assert any("cookie parameter 'sid'" in entry for entry in dropped(local))


# -- path parameters ----------------------------------------------------------


def test_a_path_parameter_sits_on_the_segment_that_names_it() -> None:
    """RAML rejects `uriParameters` naming no template in that resource's own URI."""
    local = FastAPI(title='U')

    @local.get('/books/{isbn}/cover')
    def cover(isbn: Annotated[str, Path(pattern=r'^\d{13}$')]) -> int: ...

    raml = parsed(local)
    assert list(raml.endpoints['/books/{isbn}'].uri_parameters) == ['isbn']


def test_a_path_parameter_sharing_its_segment() -> None:
    local = FastAPI(title='U')

    @local.get('/files/{name}.json')
    def f(name: str) -> int: ...

    parsed(local)
    assert dropped(local) == []


# -- parameter models (FastAPI 0.115) -----------------------------------------

models = pytest.mark.skipif(
    tuple(int(part) for part in fastapi.__version__.split('.')[:2]) < (0, 115),
    reason='query and header parameter models arrived in FastAPI 0.115',
)


class Filters(BaseModel):
    limit: int = 10
    q: str | None = None


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    a: int = 1


class Tokens(BaseModel):
    x_token: str


@models
def test_a_parameter_model_is_its_fields() -> None:
    """`get_flat_params` hands these over as bare `FieldInfo`s, which are not `Query`."""
    local = FastAPI(title='M')

    @local.get('/m')
    def m(filters: Annotated[Filters, Query()], tokens: Annotated[Tokens, Header()]) -> int: ...

    request = operation(local, '/m', 'get').request
    assert list(request.query_parameters) == ['limit', 'q']
    assert not request.query_parameters['q'].required
    assert list(request.headers) == ['x-token']
    assert dropped(local) == []


@models
def test_a_closed_parameter_model_is_reported() -> None:
    """`extra='forbid'` refuses an undeclared query parameter; `queryParameters` cannot say so."""
    local = FastAPI(title='M')

    @local.get('/m')
    def m(closed: Annotated[Closed, Query()]) -> int: ...

    assert any('refuses query parameters' in entry for entry in dropped(local))
