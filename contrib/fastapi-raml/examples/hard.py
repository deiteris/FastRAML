"""Every type construct that needed work to render, in one model.

Recursion, a tagged union, `dict[str, X]`, a `RootModel`, a two-member union, a
nullable, a bare `None`, an enum, a nested array and a subclass. `Everything` is what the
differential gate validates payloads against.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field, RootModel


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


app = FastAPI(title='Hard', version='1')


@app.post('/x')
def post_everything(body: Everything) -> Headers: ...
