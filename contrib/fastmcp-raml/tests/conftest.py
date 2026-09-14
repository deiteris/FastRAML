"""Fixtures shared by the suite.

The sample document is the repo's, not this distribution's -- `viewer` and
`tests/unit/test_bindings.py` measure themselves against the same file -- so
where it is and how it must be parsed are stated once, here.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastraml import ParseOptions, parse_from_path

from fastmcp_raml import raml_mcp

# `examples/` is not a package and is not installed, so `test_example.py` can
# only reach it from here. Running the example is how it is kept working.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'examples'))

#: The document includes from a sibling directory, so the loader's sandbox has
#: to be wider than its own.
SAMPLE_ROOT = pathlib.Path(__file__).resolve().parents[3] / 'fixtures'
SAMPLE = SAMPLE_ROOT / 'sample' / 'api.raml'
SAMPLE_OPTIONS = ParseOptions(unwrap=True, validate=True, workspace_root=SAMPLE_ROOT)

#: `baseUri` is `https://{tenant}.books.example.com/{version}`.
TENANT = {'tenant': 'acme'}


@pytest.fixture(scope='module')
def sample_path() -> pathlib.Path:
    return SAMPLE


@pytest.fixture(scope='module')
def sample_options() -> ParseOptions:
    return SAMPLE_OPTIONS


@pytest.fixture(scope='module')
def sample():
    """The parsed sample. Read-only in every test, so parsed once per module."""
    return parse_from_path(SAMPLE, SAMPLE_OPTIONS)


@pytest.fixture
def sample_server():
    # Not module-scoped: the provider owns the client it built, so the server's
    # lifespan closes it and a second session cannot reopen it.
    return raml_mcp(SAMPLE, options=SAMPLE_OPTIONS, base_uri_parameters=TENANT)


@pytest.fixture
def sample_server_without_docs():
    return raml_mcp(SAMPLE, options=SAMPLE_OPTIONS, base_uri_parameters=TENANT, include_documentation=False)
