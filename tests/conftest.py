"""Checks that span the whole test session."""

from __future__ import annotations

import pytest

from fastraml import yamlnode


@pytest.fixture(autouse=True, scope='session')
def _node_content_is_never_edited_in_place():
    """Every childless node shares one `content` list (docs/12 § 2).

    A pass that appended to a node's `content` would give every scalar in
    the process that child. Checked once, after everything has run.
    """
    yield
    assert not yamlnode._NO_CONTENT, 'a node was edited in place: the shared empty content is no longer empty'
