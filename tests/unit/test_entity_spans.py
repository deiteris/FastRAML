"""Where each entity beyond shapes is written: its key, and its whole value.

Symbol, folding and hover ranges read these, and nothing else
(`docs/archive/language-server.md` § 8, G9).
"""

from __future__ import annotations

import pytest

API = """#%RAML 1.0
title: T
documentation:
  - title: Intro
    content: Hello
traits:
  paged:
    queryParameters:
      page: integer
resourceTypes:
  collection:
    description: list
securitySchemes:
  basic:
    type: Basic Authentication
    description: basic
/items:
  type: collection
  get:
    responses:
      200:
        body:
          application/json:
            type: string
            example: hi
"""

LINES = API.splitlines()


def _line(text: str) -> int:
    return next(number for number, line in enumerate(LINES, 1) if line.strip() == text)


@pytest.fixture
def raml(memory_workspace):
    return memory_workspace.parse(memory_workspace({'api.raml': API}) / 'api.raml')


def _entities(raml):
    api = raml.entry_point
    endpoint = raml.endpoints['/items']
    operation = endpoint.operations['get']
    response = operation.responses['200']
    return {
        'trait': api.traits['paged'],
        'resource type': api.resource_types['collection'],
        'security scheme': api.security_schemes['basic'],
        'endpoint': endpoint,
        'operation': operation,
        'response': response,
        'body': response.bodies['application/json'],
    }


@pytest.mark.parametrize(
    ('entity', 'key', 'first', 'last'),
    [
        ('trait', 'paged:', 'queryParameters:', 'page: integer'),
        ('resource type', 'collection:', 'description: list', 'description: list'),
        ('security scheme', 'basic:', 'type: Basic Authentication', 'description: basic'),
        ('endpoint', '/items:', 'type: collection', 'example: hi'),
        ('operation', 'get:', 'responses:', 'example: hi'),
        ('response', '200:', 'body:', 'example: hi'),
        ('body', 'application/json:', 'type: string', 'example: hi'),
    ],
)
def test_the_key_is_the_name_and_the_value_spans_the_body(raml, entity, key, first, last):
    found = _entities(raml)[entity]
    line = _line(key)
    column = LINES[line - 1].index(key) + 1
    assert (found.key_pos.line, found.key_pos.column, found.key_pos.end_column) == (line, column, column + len(key) - 1)
    assert (found.value_pos.line, found.value_pos.end_line) == (_line(first), _line(last))
    assert found.value_pos.end_column == len(LINES[_line(last) - 1]) + 1


def test_a_documentation_item_spans_its_entry(raml):
    # A sequence entry has no key: both positions start at the entry.
    item = raml.entry_point.documentation[0]
    start = (_line('- title: Intro'), LINES[_line('- title: Intro') - 1].index('title') + 1)
    assert (item.key_pos.line, item.key_pos.column) == start
    assert (item.value_pos.line, item.value_pos.column, item.value_pos.end_line) == (*start, _line('content: Hello'))
