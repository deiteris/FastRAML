from __future__ import annotations

import json
import pathlib

import pytest

from raml_codegen import Tree, generate_from_path
from raml_codegen.targets import Settings

#: `fixtures/` is a repository-level document, not this project's: four other
#: consumers read the same file (docs/17 § 3).
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
FIXTURE = ROOT / 'fixtures' / 'sample' / 'api.raml'
WORKSPACE = ROOT / 'fixtures'
GOLDEN = pathlib.Path(__file__).resolve().parent / 'golden'


@pytest.fixture(scope='session')
def document() -> dict:
    from fastraml import ParseOptions, build_tree, parse_from_path

    return build_tree(parse_from_path(FIXTURE, ParseOptions(unwrap=True, workspace_root=WORKSPACE)))


@pytest.fixture(scope='session')
def tree(document) -> Tree:
    return Tree.of(document)


@pytest.fixture(scope='session')
def generated():
    return generate_from_path(FIXTURE, 'python', Settings(), workspace_root=WORKSPACE)


def envelope(**overrides) -> dict:
    """A minimal tree, so an envelope test does not need a parse."""
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


def written(generated, destination: pathlib.Path) -> pathlib.Path:
    generated.write(destination)
    return destination


def as_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))
