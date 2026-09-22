"""docs/12-performance.md Part 4's one hard requirement, measured.

Skipped unless `FASTRAML_BENCH=1`, the same way the TCK skips without its fixture
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
    os.environ.get('FASTRAML_BENCH') != '1',
    reason='set FASTRAML_BENCH=1 to run the benchmarks',
)

#: A quarter of the generated corpus: enough for the ratio to mean something and
#: quick enough for CI. Full-scale measurements remain a local benchmark action.
SCALE = 0.25

#: How many times each size is measured. `run_suite` already takes the best of
#: its repeats *within* one process, for the reason `bench/harness.py` gives:
#: scheduling noise is one-sided, so it can only ever make a run look slower.
#: The same argument applies across processes, and it has to, because one pair
#: of measurements can land in a window where a shared runner is busy — and a
#: window that catches the full-size run alone reads as superlinearity. CI
#: reported 1.572 from a full-size run 2.5x slower than the same class of
#: machine's own baseline, while an idle Linux box measures 1.05 to 1.08.
ATTEMPTS = 3


def _parse_seconds(scale: float) -> float:
    return run_suite(['large'], ['parse'], scale=scale, repeat=3, keep=None)[0].seconds


def test_large_is_linear_in_input_size():
    # Interleaved so a slow stretch is not concentrated on one of the two.
    full = half = float('inf')
    for _ in range(ATTEMPTS):
        full = min(full, _parse_seconds(SCALE))
        half = min(half, _parse_seconds(SCALE / 2))

    ratio = full / (2 * half)
    assert abs(ratio - 1.0) <= LINEARITY_TOLERANCE, (
        f'{full * 1e3:.1f} ms at full size against {half * 1e3:.1f} ms at half: '
        f'ratio to linear {ratio:.3f}, best of {ATTEMPTS}'
    )
