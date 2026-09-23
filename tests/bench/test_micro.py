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


_VALIDATING = [case for case in CASES if case.name.startswith('validate ')]


@pytest.mark.parametrize('case', _VALIDATING, ids=[case.name for case in _VALIDATING])
def test_every_validation_case_takes_the_path_it_names(case):
    """A value meant to conform that fails would time the failure path instead."""
    result = case.build(_resolve(case.target))()
    if 'missing' in case.name:
        assert result is not None
        assert result.head.message == 'missing required properties'
    else:
        assert result is None, result
