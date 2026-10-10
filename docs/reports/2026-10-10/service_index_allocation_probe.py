"""Attribute retained allocation to the requested-file outline cache only.

This runs the unchanged mixed driver with the harness's paused-collector policy.
It reports ownership, never instrumented elapsed time.
"""

import gc
import json
import sys
import tempfile
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from bench import corpus
from bench.service_session import exercise, prepare


def main():
    with tempfile.TemporaryDirectory(prefix='index-allocation-') as directory:
        prepared = prepare(corpus.write_hover(Path(directory), family_count=400))
        exercise(prepared)
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        tracemalloc.start()
        try:
            workspace, counts = exercise(prepared)
            assert counts['snapshots'] == 3
            gc.collect()
            before = tracemalloc.get_traced_memory()[0]
            snapshot = workspace.snapshot(prepared.root)
            snapshot.outlines.clear()
            gc.collect()
            after = tracemalloc.get_traced_memory()[0]
        finally:
            tracemalloc.stop()
            if enabled:
                gc.enable()
        print(json.dumps({'kept_before_MB': before / 1e6,
                          'kept_after_outline_release_MB': after / 1e6,
                          'outline_owned_MB': (before - after) / 1e6}, indent=2))


if __name__ == '__main__':
    main()
