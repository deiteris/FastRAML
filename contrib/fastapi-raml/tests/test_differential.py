"""The gate: rendered RAML must give the same verdict as the model it came from.

A diff against expected output misses both directions this catches -- the
document accepting what the code rejects, and rejecting what the code accepts.

Both sides validate a JSON document, which is why the pydantic call is
`model_validate_json`. `model_validate(strict=True)` in Python mode refuses a
`datetime` written as a string, which is a fact about Python objects and nothing
to do with the rendering.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any

import pytest
from fastraml import ParseOptions, parse_from_path
from pydantic import BaseModel, ValidationError

from examples import hard, library
from fastapi_raml import render

OK: dict[str, Any] = {
    'status': 'draft',
    'at': '2020-01-01T00:00:00Z',
    'tree': {'name': 'r', 'children': []},
    'pet': {'kind': 'cat', 'meows': True},
    'tags': {'a': 'b'},
    'scores': [[1]],
    'car': {'wheels': 4, 'doors': 5},
    'either': 1,
    'maybe': None,
    'nothing': None,
}


def but(**changed: Any) -> dict[str, Any]:
    return {**OK, **changed}


EVERYTHING = [
    ('baseline', OK),
    ('bad enum', but(status='gone')),
    ('bad datetime', but(at='yesterday')),
    ('deep recursion', but(tree={'name': 'r', 'children': [{'name': 'c', 'children': []}]})),
    ('recursion, bad leaf', but(tree={'name': 'r', 'children': [{'name': 1}]})),
    ('tagged: unknown tag', but(pet={'kind': 'fish', 'swims': True})),
    ('tagged: wrong member field', but(pet={'kind': 'cat', 'barks': True})),
    ('tagged: tag/body mismatch', but(pet={'kind': 'dog', 'meows': True})),
    ('tagged: bad field type', but(pet={'kind': 'cat', 'meows': 'yes'})),
    ('tagged: missing tag', but(pet={'meows': True})),
    ('dict wrong value type', but(tags={'a': 1})),
    ('array wrong depth', but(scores=[1])),
    ('subclass', but(car={'wheels': 4, 'doors': 5})),
    ('subclass, missing inherited field', but(car={'doors': 5})),
    ('subclass, missing own field', but(car={'wheels': 4})),
    ('subclass, bad inherited type', but(car={'wheels': 'four', 'doors': 5})),
    ('union: int', but(either=1)),
    ('union: str', but(either='x')),
    ('union: bool', but(either=True)),
    ('nullable as string', but(maybe='x')),
    ('nullable as int', but(maybe=3)),
    ('missing required', {key: value for key, value in OK.items() if key != 'tree'}),
]

BOOKS = [
    ('a book', {'isbn': '9' * 13, 'title': 'x'}),
    ('a book with pages', {'isbn': '9' * 13, 'title': 'x', 'pages': 5}),
    ('bad isbn', {'isbn': 'nope', 'title': 'x'}),
    ('missing title', {'isbn': '9' * 13}),
    ('pages below minimum', {'isbn': '9' * 13, 'title': 'x', 'pages': 0}),
    ('an extra property', {'isbn': '9' * 13, 'title': 'x', 'extra': 1}),
]


def shape_for(app: Any, model: type[BaseModel]) -> Any:
    """Render `app`, parse the result back, and return the shape for `model`.

    Through a file rather than a string so the failure mode matches what a
    reader would hit running `fastraml validate` on the same output.
    """
    report = render(app)
    assert not report.dropped, f'renderer dropped: {report.dropped}'
    with tempfile.TemporaryDirectory() as directory:
        source = pathlib.Path(directory) / 'api.raml'
        source.write_text(report.to_raml(), encoding='utf-8')
        raml = parse_from_path(source, ParseOptions(unwrap=True, validate=True))
        return raml.entry_point.types[model.__name__]


def agrees(shape: Any, model: type[BaseModel], value: dict[str, Any]) -> tuple[bool, bool]:
    by_raml = shape.validate(value) is None
    try:
        model.model_validate_json(json.dumps(value), strict=True)
    except ValidationError:
        return by_raml, False
    return by_raml, True


@pytest.mark.parametrize(('label', 'value'), EVERYTHING, ids=[label for label, _ in EVERYTHING])
def test_everything_agrees(label: str, value: dict[str, Any]) -> None:
    shape = shape_for(hard.app, hard.Everything)
    by_raml, by_pydantic = agrees(shape, hard.Everything, value)
    assert by_raml == by_pydantic, f'{label}: raml={by_raml} pydantic={by_pydantic}'


@pytest.mark.parametrize(('label', 'value'), BOOKS, ids=[label for label, _ in BOOKS])
def test_book_agrees(label: str, value: dict[str, Any]) -> None:
    shape = shape_for(library.app, library.Book)
    by_raml, by_pydantic = agrees(shape, library.Book, value)
    assert by_raml == by_pydantic, f'{label}: raml={by_raml} pydantic={by_pydantic}'


def test_the_cases_are_not_all_one_verdict() -> None:
    """A gate where everything passes or everything fails measures nothing."""
    shape = shape_for(hard.app, hard.Everything)
    verdicts = {shape.validate(value) is None for _, value in EVERYTHING}
    assert verdicts == {True, False}
