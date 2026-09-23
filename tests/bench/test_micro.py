"""`bench micro` cases must name functions that exist.

A case whose target cannot be resolved is reported as missing rather than
failing, so that it can run against an older checkout that predates it. The
cost of that leniency is that a rename in this tree would silently turn a
case into a no-op; this test is what catches it.
"""

from __future__ import annotations

import pytest

from bench.micro import CASES, _resolve


@pytest.mark.parametrize('case', CASES, ids=[case.name for case in CASES])
def test_every_case_resolves_here(case):
    function = _resolve(case.target)
    assert function is not None, f'{case.target} does not exist; update bench/micro.py'
    case.build(function)()
