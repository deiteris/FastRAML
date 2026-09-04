"""docs/12-performance.md Part 4's one hard requirement, measured.

Skipped unless `PYRAML_BENCH=1`, the same way the TCK skips without its fixture
directory: it costs seconds, not milliseconds, and a developer running the unit
suite in a loop should not pay for it.

Only *linearity* is asserted. Absolute time is a property of the machine and is
recorded in `bench/baselines.json` against a fingerprint, not gated here — a
wall-clock assertion in a test suite is a flake generator, and docs/12 Part 4
already calls the absolute number a goal rather than a gate. Superlinearity is
different: it means a cache is being missed, which is a bug on any machine.
"""

from __future__ import annotations

import os

import pytest

from bench.__main__ import LINEARITY_TOLERANCE, run_suite

pytestmark = pytest.mark.skipif(
    os.environ.get('PYRAML_BENCH') != '1',
    reason='set PYRAML_BENCH=1 to run the benchmarks',
)

#: A quarter of the published corpus: enough for the ratio to mean something,
#: quick enough that CI runs it on every push. The nightly job runs
#: `python -m bench linearity` at full size.
SCALE = 0.25


def test_large_is_linear_in_input_size():
    full = run_suite(['large'], ['parse'], scale=SCALE, repeat=3, keep=None)[0]
    half = run_suite(['large'], ['parse'], scale=SCALE / 2, repeat=3, keep=None)[0]

    ratio = full.seconds / (2 * half.seconds)
    assert abs(ratio - 1.0) <= LINEARITY_TOLERANCE, (
        f'{full.seconds * 1e3:.1f} ms at full size against {half.seconds * 1e3:.1f} ms at half: '
        f'ratio to linear {ratio:.3f}'
    )
