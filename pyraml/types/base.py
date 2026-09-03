"""The shape model's foundations.

Phase 2 fills this module with `BaseShape`, `Property` and `PatternProperty`
(docs/05-type-model.md). `ScalarFacet` lives here from Phase 1, because every
facet of every shape is one, and a class that `types/` holds cannot live under
`parser/` without inverting the layering.

The class carries no runtime dependency on `pyraml.parser`: `IncludeInfo` and
`DomainExtension` appear in annotations only, which `from __future__ import
annotations` keeps as strings. Building a `ScalarFacet` from YAML does need the
parser — an include has to be read, the annotated-scalar form unwrapped — so the
builder stays in `pyraml.parser.facets`. See docs/02-architecture.md section 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pyraml.positions import UNKNOWN, Position

if TYPE_CHECKING:
    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.includes import IncludeInfo

__all__ = [
    'ScalarFacet',
]


@dataclass(slots=True, eq=False)
class ScalarFacet[T]:
    """One scalar-valued facet: its value, where it came from, its annotations.

    A facet is never a bare Python value, so `minLength must be <= maxLength`
    can point at the exact line that declared each. The companion rule
    (docs/05 section 2): a facet holding a single scalar is a `ScalarFacet[T]`,
    one holding arbitrary user data is a `DataNode`.
    """

    value: T
    location: str
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: Set when the value arrived through `!include`.
    include: IncludeInfo | None = None
    #: Annotations collected from the annotated-scalar form (docs/03 section 7).
    annotations: dict[str, DomainExtension] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f'ScalarFacet({self.value!r})'
