"""Attribute mixed-scenario allocation peaks to request phases, not elapsed time.

Run this driver in each comparison tree with the same Python interpreter. Its
collector policy and request/result lifetimes match bench.service_session and
bench.harness. The wrappers only record traced allocation; this is diagnostic
evidence, never an acceptance timing.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import tempfile
import tracemalloc
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-first', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    from bench import corpus
    from bench.service_session import exercise, prepare
    from fastraml.service import inlays, outline, queries
    from fastraml.service.workspace import Workspace

    with tempfile.TemporaryDirectory(prefix='inlay-allocation-') as directory:
        prepared = prepare(corpus.write_hover(Path(directory), family_count=400))
        # Warm configuration/imports, as the ordinary harness does before tracing.
        exercise(prepared, source_first=args.source_first)
        gc.collect()
        rows = []
        version = [0]

        def watched(name, operation):
            def request(*arguments, **kwargs):
                before = tracemalloc.get_traced_memory()[0]
                tracemalloc.reset_peak()
                result = operation(*arguments, **kwargs)
                current, peak = tracemalloc.get_traced_memory()
                rows.append({'version': version[0], 'request': name,
                             'before_MB': before / 1e6, 'after_MB': current / 1e6,
                             'peak_MB': peak / 1e6})
                return result
            return request

        change = Workspace.change

        def changed(workspace, uri, text, new_version):
            version[0] = new_version
            return change(workspace, uri, text, new_version)

        with ExitStack() as stack:
            stack.enter_context(patch.object(Workspace, 'change', changed))
            for owner, method, name in (
                (Workspace, '_parse', 'snapshot'),
                (outline, 'document_symbols', 'outline'),
                (inlays, 'inlay_hints', 'inlays'),
                (Workspace, 'source', 'source'),
                (queries, 'hover', 'hover'),
            ):
                stack.enter_context(patch.object(owner, method, watched(name, getattr(owner, method))))
            enabled = gc.isenabled()
            gc.disable()
            tracemalloc.start()
            try:
                result = exercise(prepared, source_first=args.source_first)
                gc.collect()
                kept = tracemalloc.get_traced_memory()[0]
                assert result[1]['snapshots'] == 3
            finally:
                tracemalloc.stop()
                if enabled:
                    gc.enable()
        print(json.dumps({'source_first': args.source_first, 'kept_MB': kept / 1e6, 'requests': rows}, indent=2))


if __name__ == '__main__':
    main()
