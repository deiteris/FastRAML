"""The mutation corpus runs only when asked for, with `--mutations`.

It is an exploration, not a test: random mistakes in valid documents find
defects by volume, and each one found gets a test of its own (docs/14 § 3).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _asked_for(request: pytest.FixtureRequest) -> None:
    if not request.config.getoption('--mutations'):
        pytest.skip('the mutation corpus is an exploration: run it with --mutations')
