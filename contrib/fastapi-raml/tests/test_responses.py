"""Bodies and responses: what a route answers, and what `responses=` adds.

`test_what_the_route_writes_validates` is the differential for this half: the
route really answers, and its body must validate against the RAML type its
status code declares. A response is what the model *writes* -- by
serialization alias, without `exclude=True` fields, with computed fields -- so
a document built from what the model reads would fail it.
"""

import dataclasses
from typing import Annotated, Literal, NotRequired, TypedDict

import pytest
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, computed_field

from fastapi_raml import render
from tests.support import dropped, operation, parsed


class User(BaseModel):
    user_name: str = Field(serialization_alias='userName')
    pin: str = Field(exclude=True)
    first: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def initial(self) -> str:
        return self.first[:1]


class Team(BaseModel):
    members: list[User]


app = FastAPI(title='R')


@app.get('/user')
def get_user() -> User:
    return User(user_name='ada', pin='1234', first='Ada')


@app.get('/team')
def get_team() -> Team:
    return Team(members=[User(user_name='ada', pin='1', first='Ada')])


@app.post('/user', status_code=201)
def add_user(user: User) -> User:
    return user


@dataclasses.dataclass
class Point:
    x: int
    label: str = 'origin'


class Movie(TypedDict):
    title: str
    year: NotRequired[int]


@app.get('/point')
def get_point() -> Point:
    return Point(x=1)


@app.get('/movie')
def get_movie() -> Movie:
    return {'title': 'M'}


CASES = [('/user', 'get', '200'), ('/team', 'get', '200'), ('/point', 'get', '200'), ('/movie', 'get', '200')]


@pytest.mark.parametrize(('path', 'verb', 'code'), CASES, ids=[path for path, _, _ in CASES])
def test_what_the_route_writes_validates(path: str, verb: str, code: str) -> None:
    shape = operation(app, path, verb).responses[code].bodies['application/json'].shape
    body = TestClient(app).request(verb, path).json()
    assert shape.validate(body) is None, body


def test_a_dataclass_and_a_typeddict_are_described() -> None:
    """Neither is a pydantic model; FastAPI serves both, and both used to be `any`."""
    assert operation(app, '/point', 'get').responses['200'].bodies['application/json'].shape.validate({'x': 'a'})
    assert dropped(app) == []


def test_the_request_body_is_what_the_model_reads() -> None:
    """The same model, other direction: `user_name`, a pin, no computed field."""
    shape = operation(app, '/user', 'post').request.bodies['application/json'].shape
    assert shape.validate({'user_name': 'ada', 'pin': '1', 'first': 'Ada'}) is None
    assert shape.validate({'userName': 'ada', 'first': 'Ada'}) is not None


# -- status codes without a model ---------------------------------------------


def test_a_no_content_route_declares_its_status_and_no_body() -> None:
    local = FastAPI(title='R')

    @local.delete('/x/{i}', status_code=204)
    def delete(i: int) -> None: ...

    response = operation(local, '/x/{i}', 'delete').responses['204']
    assert response.bodies == {}


def test_a_route_with_no_response_model_still_answers_json() -> None:
    """FastAPI's schema says `application/json` of anything; silence would say no response."""
    local = FastAPI(title='R')

    @local.get('/raw')
    def raw():
        return {'a': 1}

    assert list(operation(local, '/raw', 'get').responses['200'].bodies) == ['application/json']


def test_a_raw_response_class_has_no_body_to_describe() -> None:
    local = FastAPI(title='R')

    @local.get('/raw', response_class=Response)
    def raw() -> Response: ...

    assert operation(local, '/raw', 'get').responses['200'].bodies == {}


def test_an_html_route_answers_text() -> None:
    local = FastAPI(title='R')

    @local.get('/page', response_class=HTMLResponse)
    def page() -> str: ...

    body = operation(local, '/page', 'get').responses['200'].bodies['text/html']
    assert body.shape.validate('<p>') is None


def test_a_response_description_is_written_when_the_route_gives_one() -> None:
    local = FastAPI(title='R')

    @local.get('/a', response_description='the thing')
    def a() -> int: ...

    @local.get('/b')
    def b() -> int: ...

    assert operation(local, '/a', 'get').responses['200'].description.value == 'the thing'
    assert operation(local, '/b', 'get').responses['200'].description is None


# -- responses= ---------------------------------------------------------------


class Error(BaseModel):
    code: int = Field(serialization_alias='errorCode')


def test_an_extra_response_model_is_what_it_writes() -> None:
    local = FastAPI(title='R')

    @local.get('/x', responses={404: {'model': Error, 'description': 'missing'}})
    def x() -> int: ...

    response = operation(local, '/x', 'get').responses['404']
    assert response.description.value == 'missing'
    assert response.bodies['application/json'].shape.validate({'errorCode': 1}) is None


def test_extra_response_content_names_its_media_types() -> None:
    local = FastAPI(title='R')

    @local.get('/x', responses={200: {'content': {'image/png': {}}}})
    def x() -> int: ...

    assert sorted(operation(local, '/x', 'get').responses['200'].bodies) == ['application/json', 'image/png']


@pytest.mark.parametrize(
    ('key', 'expected'),
    [('default', 'a default response has no RAML form'), ('4XX', "the status range '4XX' has no RAML form")],
)
def test_a_response_key_raml_cannot_spell_is_reported_by_what_it_is(key: str, expected: str) -> None:
    local = FastAPI(title='R')

    @local.get('/x', responses={key: {'description': 'x'}})
    def x() -> int: ...

    assert any(expected in entry for entry in dropped(local))
    parsed(local)


def test_a_json_schema_in_response_content_is_reported() -> None:
    local = FastAPI(title='R')

    @local.get('/x', responses={200: {'content': {'text/csv': {'schema': {'type': 'string'}}}}})
    def x() -> int: ...

    assert any('JSON Schema for text/csv' in entry for entry in dropped(local))


# -- request bodies -----------------------------------------------------------


def test_an_upload_is_a_raml_file() -> None:
    local = FastAPI(title='R')

    @local.post('/up')
    def up(one: UploadFile, many: list[UploadFile], note: Annotated[str, Form()]) -> int: ...

    body = render(local).document.types
    (form,) = (decl for name, decl in body.items() if name.startswith('Body_'))
    assert form.properties['one'].type == 'file'
    assert form.properties['many'].type == 'file[]'
    assert list(operation(local, '/up', 'post').request.bodies) == ['multipart/form-data']
    assert dropped(local) == []


def test_raw_bytes_from_a_form_are_a_file_too() -> None:
    local = FastAPI(title='R')

    @local.post('/up')
    def up(data: Annotated[bytes, File()]) -> int: ...

    parsed(local)
    assert dropped(local) == []


class _Cat(BaseModel):
    kind: Literal['cat']
    meows: bool


class _Dog(BaseModel):
    kind: Literal['dog']
    barks: bool


def test_a_tagged_union_body_keeps_its_discriminator() -> None:
    """The body goes through `field`, as a property does, so what the parameter says is read."""
    local = FastAPI(title='R')

    @local.post('/pet')
    def pet(pet: Annotated[_Cat | _Dog, Field(discriminator='kind')]) -> int: ...

    types = render(local).document.types
    assert (types['_Cat'].discriminator_value, types['_Dog'].discriminator_value) == ('cat', 'dog')
    parsed(local)
