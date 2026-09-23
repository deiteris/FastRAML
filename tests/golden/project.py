"""The golden layer's view of a parsed model.

`fastraml.views.tree` is the projection; this names it for the golden harness and
adds nothing. The goldens depend on it being driven off `__slots__`, so a facet
added to a kind cannot go missing from it.

Positions come from `positions_of` and are written to a separate file, so an
edit to one document cannot churn a position golden nobody is reading
(docs/14 § 3).
"""

from __future__ import annotations

from fastraml.views.tree import build_tree as project
from fastraml.views.tree import positions_of

__all__ = ['positions_of', 'project']
