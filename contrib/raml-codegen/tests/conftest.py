from __future__ import annotations

import json
import pathlib

import pytest

from raml_codegen import Settings, Tree, generate

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / 'golden'

#: `fastraml tree fixtures/sample/api.raml -w fixtures`, committed.
#:
#: The suite reads this rather than parsing, because the package does not depend
#: on the parser and neither should its tests. `viewer/public/api.json` is the
#: same arrangement, and the root's `tests/unit/test_bindings.py` keeps both
#: from going stale.
TREE = HERE / 'api.json'


@pytest.fixture(scope='session')
def document() -> dict:
    return json.loads(TREE.read_text(encoding='utf-8'))


@pytest.fixture(scope='session')
def tree(document) -> Tree:
    return Tree.of(document)


@pytest.fixture(scope='session')
def generated(document):
    return generate(document, 'python', Settings())


def envelope(**overrides) -> dict:
    """A minimal tree, so an envelope test needs no fixture at all."""
    return {
        'format': 'fastraml-tree',
        'format_version': 1,
        'view': 'effective',
        'base': 'fastraml://id',
        'entry_point': None,
        'types': {},
        'annotation_types': {},
        'security_schemes': {},
        'endpoints': {},
        'annotations': [],
        **overrides,
    }
