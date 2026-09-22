"""Every type construct that needed work to render, in one model.

Recursion, a tagged union, `dict[str, X]`, a `RootModel`, a two-member union, a
nullable, a bare `None`, an enum, a nested array and a subclass. `Everything` is what the
differential gate validates payloads against.

The models are the same file `fastapi-raml` keeps, deliberately: two
integrations reading the same models must produce the same `types:`, and a
divergence is a bug in one of them.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from aiohttp import web
from pydantic import BaseModel, Field, RootModel

from aiohttp_raml import RamlView, Responds


class Status(StrEnum):
    draft = 'draft'
    live = 'live'


class Node(BaseModel):
    """Self-recursive: `model_json_schema()` alone loses this body."""

    name: str
    children: list['Node'] = []


class Vehicle(BaseModel):
    """A base other models extend, so RAML gets a real supertype to name."""

    wheels: int


class Car(Vehicle):
    """`type: Vehicle`, declaring only what it adds."""

    doors: int


class Cat(BaseModel):
    kind: Literal['cat'] = 'cat'
    meows: bool


class Dog(BaseModel):
    kind: Literal['dog'] = 'dog'
    barks: bool


Pet = Annotated[Cat | Dog, Field(discriminator='kind')]


class Headers(RootModel[dict[str, str]]):
    """A root model over a mapping: RAML's `//` pattern-any property."""


class Everything(BaseModel):
    status: Status
    at: datetime
    tree: Node
    pet: Pet
    tags: dict[str, str]
    scores: list[list[int]]
    car: Car
    either: int | str
    maybe: str | None
    nothing: None = None


class EverythingView(RamlView):
    async def post(self, body: Everything) -> Annotated[web.Response, Responds(200, Headers)]:
        return web.json_response({})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_view('/x', EverythingView)
    return app


app = build_app()
METADATA = {'title': 'Hard', 'version': '1'}
