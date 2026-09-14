"""The benchmark suite (docs/12-performance.md Part 4, docs/14-testing.md section 5).

Not imported by `fastraml`, and not part of the wheel. It lives beside the package
rather than under `tests/` because its corpora are generated inputs measured in
seconds, not assertions.

Run it with `python -m bench`.
"""

from __future__ import annotations
