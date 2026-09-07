"""The golden layer's view of a parsed model.

`pyraml.views.tree` is the projection; this names it for the golden harness and
adds nothing. It was test-only until consumers needed the same thing, and the
property that made it worth promoting is the one the goldens depend on: it is
driven off `__slots__`, so a facet added to a kind cannot go missing from it.

Positions come from `positions_of` and are written to a separate file, so an
edit to one document cannot churn a position golden nobody is reading
(docs/14 § 2).
"""

from __future__ import annotations

from pyraml.views.tree import build_tree as project
from pyraml.views.tree import positions_of

__all__ = ['positions_of', 'project']
