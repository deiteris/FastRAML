"""The linearity bound of docs/12-performance.md § 5, in time and memory.

Skipped unless `FASTRAML_BENCH=1`, the same way the TCK skips without its fixture
directory: it costs seconds, not milliseconds, and a developer running the unit
suite in a loop should not pay for it.

Only *linearity* is asserted. Absolute time is a property of the machine and is
recorded in `bench/baselines.json` against a fingerprint, not gated here: a
wall-clock assertion in a test suite is a flake generator (docs/12 § 5).
Superlinearity is different: it means a cache is being missed, which is a bug
on any machine.
"""

from __future__ import annotations

import os

import pytest

from bench.__main__ import LINEARITY_CONFIGS, LINEARITY_TOLERANCE, measure_linearity

pytestmark = pytest.mark.skipif(
    os.environ.get('FASTRAML_BENCH') != '1',
    reason='set FASTRAML_BENCH=1 to run the benchmarks',
)

#: A fifth of the generated corpus: enough for the ratio to mean something and
#: quick enough for CI. Full-scale measurements remain a local benchmark action.
#: Chosen so every workload's counts halve exactly: at 0.25, `unions` has 15
#: families and its "half" rounds 7.5 to 8, which read as sublinear memory.
SCALE = 0.2

#: How many times each size is measured. `run_suite` already takes the best of
#: its repeats *within* one process, for the reason `bench/harness.py` gives:
#: scheduling noise is one-sided, so it can only ever make a run look slower.
#: The same argument applies across processes, and it has to, because one pair
#: of measurements can land in a window where a shared runner is busy — and a
#: window that catches the full-size run alone reads as superlinearity. CI
#: reported 1.572 from a full-size run 2.5x slower than the same class of
#: machine's own baseline, while an idle Linux box measures 1.05 to 1.08.
ATTEMPTS = 3


@pytest.mark.parametrize('name', sorted(LINEARITY_CONFIGS))
def test_linear_in_input_size_in_time_and_memory(name):
    """`large` for the general pipeline; each feature workload for its own code.

    Memory is checked twice (`bench/harness.py`): the peak of one build with the
    collector paused, and what the result retains. A cache kept on the model, or
    a copy per union member, grows them; superlinear growth there is the same
    bug as in time, and it does not hide behind a noisy machine.
    """
    result = measure_linearity(name, scale=SCALE, repeat=3, attempts=ATTEMPTS)
    assert not result.failures(), (
        f'{name}/{result.config}: {result.full.seconds * 1e3:.1f} ms and '
        f'{result.full.retained_bytes / 1e6:.1f} MB retained at full size against '
        f'{result.half.seconds * 1e3:.1f} ms and {result.half.retained_bytes / 1e6:.1f} MB at half: '
        f'{", ".join(result.failures())} (tolerance {LINEARITY_TOLERANCE}), best of {ATTEMPTS}'
    )
